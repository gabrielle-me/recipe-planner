"""
Recipe Ingestor – Private Streamlit MVP
--------------------------------------
Ziel: Rezepte aus URL, Text, PDF oder Screenshot/Foto in eine einheitliche Struktur bringen, lokal speichern (SQLite), durchsuchen und Einkaufslisten generieren.

Setup (Python 3.10+ empfohlen):
1) python -m venv .venv && source .venv/bin/activate  (Windows: .venv\Scripts\activate)
2) pip install -r requirements.txt  (alternativ: siehe Liste unten)
3) streamlit run app.py

requirements.txt (Inhalt):
streamlit
requests
beautifulsoup4
extruct
w3lib
trafilatura
readability-lxml
lxml
pytesseract
pillow
pdfplumber
pandas
python-slugify
sqlalchemy
python-dateutil

Optional:
- Für bessere PDF/OCR-Resultate: Tesseract installieren (System-Paket). macOS: brew install tesseract, Ubuntu: sudo apt-get install tesseract-ocr, Windows: Installer.

Datenhaltung:
- SQLite-Datei: data/recipes.db
- Tabellen: recipes, ingredients, steps, tags

Hinweis:
- Erst versucht der Parser, schema.org/Recipe (JSON-LD/Microdata) aus Webseiten zu lesen. Fallback ist Heuristik (Zutaten-/Zubereitungserkennung).
- Für private Nutzung; kein externer AI-Call erforderlich.
"""

import os
import io
import re
import json
import uuid
import time
import base64
import requests
import pdfplumber
import pytesseract
import pandas as pd
import streamlit as st
from PIL import Image
from slugify import slugify
from bs4 import BeautifulSoup
from w3lib.html import get_base_url
from dateutil import parser as dateparser

# HTML parsers
import extruct
import trafilatura
from readability import Document

# DB
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

APP_TITLE = "Rezept‑Importer (Lokal)"
DB_PATH = "data/recipes.db"
os.makedirs("data", exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS recipes (
    id TEXT PRIMARY KEY,
    title TEXT,
    source_url TEXT,
    source_type TEXT,
    servings TEXT,
    total_time TEXT,
    image_url TEXT,
    raw_text TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS ingredients (
    id TEXT PRIMARY KEY,
    recipe_id TEXT,
    line TEXT,
    FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS steps (
    id TEXT PRIMARY KEY,
    recipe_id TEXT,
    idx INTEGER,
    instruction TEXT,
    FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    recipe_id TEXT,
    tag TEXT,
    FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
);
"""

with engine.begin() as conn:
    for stmt in SCHEMA_SQL.strip().split(";"):
        if stmt.strip():
            conn.execute(text(stmt))

# --------------------------
# Utility
# --------------------------

def aqm_now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")

RE_ING_HEADER = re.compile(r"^(zutaten|ingredients)\b", re.I)
RE_STEPS_HEADER = re.compile(r"^(zubereitung|anleitung|directions|method)\b", re.I)
BULLET = re.compile(r"^\s*([\-•\*\u2022\u2023\u2043]|\d+\.)\s+")


def clean_lines(text: str):
    lines = [re.sub(r"\s+", " ", l.strip()) for l in text.splitlines()]
    return [l for l in lines if l]


def guess_sections(lines):
    """Naive Erkennung von Zutaten/Schritte anhand Überschriften & Aufzählungen."""
    ingredients, steps = [], []
    mode = None
    for l in lines:
        if RE_ING_HEADER.search(l):
            mode = "ing"
            continue
        if RE_STEPS_HEADER.search(l):
            mode = "steps"
            continue
        # bullets als Zutaten/Steps erkennen
        if mode == "ing":
            if BULLET.search(l) or re.match(r"^\d*\s?[a-zA-ZäöüÄÖÜ]+", l):
                ingredients.append(l)
            else:
                # Wechsel wenn Fließtext -> Steps
                if l and len(l.split()) > 3:
                    mode = "steps"
        if mode == "steps":
            if l:
                steps.append(l)
    # Fallback: wenn keine Header, versuche erste Bullet-Blöcke als Zutaten
    if not ingredients:
        ing_block = []
        for l in lines:
            if BULLET.search(l):
                ing_block.append(BULLET.sub("", l))
            elif ing_block:
                break
        if ing_block:
            ingredients = ing_block
            # Rest als steps
            idx = lines.index(ing_block[-1]) if ing_block[-1] in lines else 0
            steps = [l for l in lines[idx+1:] if l]
    return ingredients, steps


# --------------------------
# Extractors
# --------------------------

def extract_from_jsonld(data):
    """Map schema.org Recipe -> dict."""
    def norm(v):
        if isinstance(v, list):
            return ", ".join([str(x) for x in v])
        return v

    title = data.get("name") or data.get("headline")
    img = data.get("image")
    if isinstance(img, dict):
        image_url = img.get("url")
    elif isinstance(img, list):
        image_url = img[0] if img else None
    else:
        image_url = img
    servings = norm(data.get("recipeYield"))
    total_time = norm(data.get("totalTime"))
    ingredients = data.get("recipeIngredient") or []
    instructions = []
    inst = data.get("recipeInstructions")
    if isinstance(inst, list):
        for item in inst:
            if isinstance(item, dict):
                instructions.append(item.get("text") or "")
            else:
                instructions.append(str(item))
    elif isinstance(inst, str):
        instructions = [inst]

    return {
        "title": title,
        "image_url": image_url,
        "servings": servings,
        "total_time": total_time,
        "ingredients": [i.strip() for i in ingredients if i and i.strip()],
        "steps": [s.strip() for s in instructions if s and s.strip()],
    }


def extract_from_url(url: str):
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    html = resp.text
    base = get_base_url(html, url)

    # 1) JSON-LD/Microdata
    data = extruct.extract(html, base_url=base, syntaxes=["json-ld", "microdata", "opengraph"]) 
    for blob in data.get("json-ld", []) + data.get("microdata", []):
        node = blob.get("@graph") if isinstance(blob.get("@graph"), list) else [blob]
        for item in node:
            t = item.get("@type")
            types = t if isinstance(t, list) else [t]
            if any(x in ("Recipe",) for x in types):
                parsed = extract_from_jsonld(item)
                if parsed.get("title"):
                    return parsed, html

    # 2) Readability/Trafila – Heuristik
    try:
        readable = Document(html).summary(html_partial=True)
        soup = BeautifulSoup(readable, "lxml")
        title = Document(html).short_title()
    except Exception:
        soup = BeautifulSoup(html, "lxml")
        title = soup.title.string.strip() if soup.title else None

    main_text = trafilatura.extract(html) or soup.get_text("\n")
    lines = clean_lines(main_text)
    ingredients, steps = guess_sections(lines)

    return {
        "title": title,
        "image_url": None,
        "servings": None,
        "total_time": None,
        "ingredients": ingredients,
        "steps": steps,
    }, html


def extract_from_pdf(file_bytes: bytes):
    text_parts = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            text_parts.append(txt)
    text = "\n".join(text_parts)
    lines = clean_lines(text)
    ingredients, steps = guess_sections(lines)
    title = None
    if lines:
        title = lines[0][:120]
    return {
        "title": title,
        "image_url": None,
        "servings": None,
        "total_time": None,
        "ingredients": ingredients,
        "steps": steps,
    }, text


def extract_from_image(file_bytes: bytes):
    img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    text = pytesseract.image_to_string(img, lang="deu+eng")
    lines = clean_lines(text)
    ingredients, steps = guess_sections(lines)
    title = lines[0][:120] if lines else "OCR Rezept"
    return {
        "title": title,
        "image_url": None,
        "servings": None,
        "total_time": None,
        "ingredients": ingredients,
        "steps": steps,
    }, text


def extract_from_text(text: str):
    lines = clean_lines(text)
    ingredients, steps = guess_sections(lines)
    title = lines[0][:120] if lines else "Freitext Rezept"
    return {
        "title": title,
        "image_url": None,
        "servings": None,
        "total_time": None,
        "ingredients": ingredients,
        "steps": steps,
    }, text


# --------------------------
# Storage
# --------------------------

def save_recipe(data: dict, source_type: str, source_url: str | None, raw_text: str | None):
    rid = str(uuid.uuid4())
    title = data.get("title") or "Unbenanntes Rezept"
    servings = data.get("servings")
    total_time = data.get("total_time")
    image_url = data.get("image_url")
    ingredients = data.get("ingredients") or []
    steps = data.get("steps") or []

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO recipes(id,title,source_url,source_type,servings,total_time,image_url,raw_text,created_at)
            VALUES(:id,:title,:source_url,:source_type,:servings,:total_time,:image_url,:raw_text,:created_at)
        """), dict(
            id=rid,
            title=title,
            source_url=source_url,
            source_type=source_type,
            servings=servings,
            total_time=total_time,
            image_url=image_url,
            raw_text=raw_text,
            created_at=aqm_now_iso(),
        ))
        for line in ingredients:
            conn.execute(text("""
                INSERT INTO ingredients(id,recipe_id,line) VALUES(:id,:rid,:line)
            """), dict(id=str(uuid.uuid4()), rid=rid, line=line))
        for idx, step in enumerate(steps):
            conn.execute(text("""
                INSERT INTO steps(id,recipe_id,idx,instruction) VALUES(:id,:rid,:idx,:ins)
            """), dict(id=str(uuid.uuid4()), rid=rid, idx=idx, ins=step))
    return rid


def load_recipes(search: str | None = None):
    q = "SELECT id,title,servings,total_time,created_at FROM recipes ORDER BY created_at DESC"
    params = {}
    if search:
        q = "SELECT id,title,servings,total_time,created_at FROM recipes WHERE title LIKE :q ORDER BY created_at DESC"
        params = {"q": f"%{search}%"}
    with engine.begin() as conn:
        rows = conn.execute(text(q), params).fetchall()
    return rows


def get_recipe_detail(rid: str):
    with engine.begin() as conn:
        r = conn.execute(text("SELECT * FROM recipes WHERE id=:id"), {"id": rid}).fetchone()
        ings = conn.execute(text("SELECT line FROM ingredients WHERE recipe_id=:id"), {"id": rid}).fetchall()
        steps = conn.execute(text("SELECT idx,instruction FROM steps WHERE recipe_id=:id ORDER BY idx ASC"), {"id": rid}).fetchall()
    return r, [i[0] for i in ings], [s[1] for s in steps]


# --------------------------
# UI
# --------------------------

st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

with st.sidebar:
    st.markdown("**Import-Quellen**: URL · Text · PDF · Bild/Screenshot")
    st.markdown("**Tipp**: Erst URL versuchen – viele Seiten haben bereits Recipe-JSON-LD.")


tabs = st.tabs(["+ Import", "📚 Rezepte", "🛒 Einkaufsliste"])

# --- Import Tab ---
with tabs[0]:
    st.subheader("Rezept hinzufügen")
    src = st.radio("Quelle", ["URL", "Text", "PDF", "Bild"], horizontal=True)

    if src == "URL":
        url = st.text_input("Rezept-URL")
        if st.button("Aus URL importieren", disabled=not url):
            try:
                data, raw = extract_from_url(url)
                rid = save_recipe(data, source_type="url", source_url=url, raw_text=raw)
                st.success(f"Gespeichert: {data.get('title') or 'Rezept'}")
            except Exception as e:
                st.error(f"Fehler: {e}")

    elif src == "Text":
        txt = st.text_area("Rezept-Text einfügen", height=220, placeholder="Titel optional in erster Zeile, dann Zutaten / Zubereitung…")
        if st.button("Aus Text importieren", disabled=not txt.strip()):
            try:
                data, raw = extract_from_text(txt)
                rid = save_recipe(data, source_type="text", source_url=None, raw_text=raw)
                st.success(f"Gespeichert: {data.get('title') or 'Rezept'}")
            except Exception as e:
                st.error(f"Fehler: {e}")

    elif src == "PDF":
        up = st.file_uploader("PDF hochladen", type=["pdf"])
        if up and st.button("Aus PDF importieren"):
            try:
                data, raw = extract_from_pdf(up.read())
                rid = save_recipe(data, source_type="pdf", source_url=None, raw_text=raw)
                st.success(f"Gespeichert: {data.get('title') or 'Rezept'}")
            except Exception as e:
                st.error(f"Fehler: {e}")

    elif src == "Bild":
        ups = st.file_uploader("Screenshots/Bilder hochladen (mehrere möglich)", type=["png","jpg","jpeg","webp"], accept_multiple_files=True)
        if ups and st.button("Aus Bildern (OCR) importieren"):
            try:
                texts = []
                for up in ups:
                    img = Image.open(up).convert("RGB")
                    texts.append(pytesseract.image_to_string(img, lang="deu+eng"))
                combined_text = "".join(texts)
                data, raw = extract_from_text(combined_text)
                rid = save_recipe(data, source_type="image", source_url=None, raw_text=combined_text)
                st.success(f"Gespeichert: {data.get('title') or 'Rezept'}")
            except Exception as e:
                st.error(f"Fehler: {e}")

# --- Library Tab ---
with tabs[1]:
    c1, c2 = st.columns([2,3])
    with c1:
        s = st.text_input("Suche nach Titel")
        rows = load_recipes(s)
        df = pd.DataFrame(rows, columns=["id","Titel","Portionen","Zeit","Erstellt"])
        st.dataframe(df.drop(columns=["id"]))
        selected = st.selectbox("Rezept öffnen", options=["-"] + [r[0] for r in rows], format_func=lambda x: "-" if x=="-" else next((r[1] for r in rows if r[0]==x), x))
    with c2:
        if selected and selected != "-":
            r, ings, steps = get_recipe_detail(selected)
            st.markdown(f"### {r.title}")
            meta = []
            if r.servings: meta.append(f"Portionen: {r.servings}")
            if r.total_time: meta.append(f"Zeit: {r.total_time}")
            if meta:
                st.caption(" · ".join(meta))
            cols = st.columns(2)
            with cols[0]:
                st.markdown("**Zutaten**")
                st.markdown("\n".join([f"- {i}" for i in ings]) or "—")
            with cols[1]:
                st.markdown("**Zubereitung**")
                st.markdown("\n".join([f"{idx+1}. {s}" for idx, s in enumerate(steps)]) or "—")
            # --- Portionen anpassen & Rezept überschreiben ---
            with st.expander("🍽️ Portionen anpassen"):
                import re as _re
                UNICODE_FRACTIONS = {"¼":0.25,"½":0.5,"¾":0.75,"⅓":1/3,"⅔":2/3,"⅛":0.125,"⅜":0.375,"⅝":0.625,"⅞":0.875}
                UNITS_ROUNDING = {
                    # ganzzahlig
                    "g": lambda x: round(x), "gramm": lambda x: round(x), "ml": lambda x: round(x),
                    "stk": lambda x: round(x), "stück": lambda x: round(x),
                    # halbe Schritte
                    "el": lambda x: round(x*2)/2, "tl": lambda x: round(x*2)/2,
                    "esslöffel": lambda x: round(x*2)/2, "teelöffel": lambda x: round(x*2)/2,
                    # zwei Nachkommastellen
                    "kg": lambda x: round(x,2), "l": lambda x: round(x,2),
                    "tasse": lambda x: round(x*2)/2, "cup": lambda x: round(x*2)/2,
                }

                def _extract_servings_num_generic(serv_raw):
                    if not serv_raw: return None
                    m = _re.search(r"(\d+[\.,]?\d*)", str(serv_raw))
                    return float(m.group(1).replace(",", ".")) if m else None

                def _unicode_fracs_to_float(txt):
                    if not txt: return None
                    m = _re.match(r"^\s*(\d+)?\s*([{}])".format("".join(UNICODE_FRACTIONS.keys())), txt)
                    if not m: return None
                    return (float(m.group(1)) if m.group(1) else 0.0) + UNICODE_FRACTIONS.get(m.group(2), 0)

                def _parse_leading_quantity(line):
                    s = line.lstrip(); off = len(line) - len(s)
                    # Range 2-3 / 2–3
                    m = _re.match(r"(\d+[\.,]?\d*)\s*[\-–—]\s*(\d+[\.,]?\d*)", s)
                    if m:
                        a = float(m.group(1).replace(",", ".")); b = float(m.group(2).replace(",", "."))
                        return ("range", off+m.start(0), off+m.end(0), (a,b), ("," in m.group(1)))
                    # Gemischter Bruch 1 1/2
                    m = _re.match(r"(\d+)\s+(\d+)/(\d+)", s)
                    if m:
                        val = float(m.group(1)) + float(m.group(2))/float(m.group(3))
                        return ("single", off+m.start(0), off+m.end(0), val, False)
                    # Unicode‑Bruch ½ / 1 ½
                    uf = _unicode_fracs_to_float(s[:4])
                    if uf is not None:
                        m = _re.match(r"\s*(\d+)?\s*[{}]".format("".join(UNICODE_FRACTIONS.keys())), s)
                        return ("single", off+m.start(0), off+m.end(0), uf, False)
                    # Einfache Zahl
                    m = _re.match(r"(\d+[\.,]?\d*)", s)
                    if m:
                        val = float(m.group(1).replace(",", ".")); had_comma = ("," in m.group(1))
                        return ("single", off+m.start(1), off+m.end(1), val, had_comma)
                    return None

                def _detect_unit_after(line, end_idx):
                    tail = line[end_idx:].strip().lower()
                    m = _re.match(r"([a-zäöüA-ZÄÖÜ]+)", tail)
                    if not m: return None
                    return m.group(1).replace("ä","ae").replace("ö","oe").replace("ü","ue")

                def _format_number(value, prefer_comma):
                    txt = (f"{value:.2f}" if abs(value-round(value))>1e-6 else str(int(round(value))))
                    txt = txt.rstrip("0").rstrip(".")
                    return txt.replace(".", ",") if prefer_comma else txt

                def _apply_round(unit, x):
                    func = UNITS_ROUNDING.get(unit) if unit else None
                    return func(x) if func else x

                def scale_line_smart(line, factor):
                    m = _parse_leading_quantity(line)
                    if not m: return line
                    kind,start,end,val,had_comma = m
                    unit = _detect_unit_after(line, end)
                    if kind == "range":
                        a,b = val
                        a = _apply_round(unit, a*factor); b = _apply_round(unit, b*factor)
                        return line[:start] + f"{_format_number(a,had_comma)}–{_format_number(b,had_comma)}" + line[end:]
                    x = _apply_round(unit, val*factor)
                    return line[:start] + _format_number(x,had_comma) + line[end:]

                base_serv = _extract_servings_num_generic(r.servings)
                default_target = int(base_serv) if base_serv else 2
                target = st.number_input("Ziel‑Portionen", min_value=1, value=default_target, step=1)

                if base_serv and target and target != base_serv:
                    factor = float(target) / float(base_serv)
                    scaled = [scale_line_smart(l, factor) for l in ings]
                    st.markdown("**Skalierte Zutaten**")
                    st.markdown("\n".join([f"- {i}" for i in scaled]))

                    if st.button("Skalierung übernehmen (Rezept überschreiben)"):
                        with engine.begin() as conn:
                            conn.execute(text("UPDATE recipes SET servings=:s WHERE id=:id"),
                                        {"s": f"{int(target)} Portionen", "id": selected})
                            conn.execute(text("DELETE FROM ingredients WHERE recipe_id=:id"), {"id": selected})
                            for line in scaled:
                                conn.execute(text("INSERT INTO ingredients(id,recipe_id,line) VALUES(:id,:rid,:line)"),
                                            {"id": str(uuid.uuid4()), "rid": selected, "line": line})
                        st.success("Rezept aktualisiert.")
                        st.rerun()
                elif not base_serv:
                    st.info("Keine Basis‑Portionsangabe gefunden. Trage sie oben im Rezept ein (z. B. '2 Portionen'), dann klappt die Skalierung.")
            if r.source_url:
                st.link_button("Quelle öffnen", r.source_url)

# --- Shopping List Tab ---
with tabs[2]:
    st.subheader("Einkaufsliste aus ausgewählten Rezepten")
    rows = load_recipes()
    choices = st.multiselect("Rezepte auswählen", options=[r[0] for r in rows], format_func=lambda x: next((r[1] for r in rows if r[0]==x), x))
    if choices:
        all_ings = []
        for rid in choices:
            _, ings, _ = get_recipe_detail(rid)
            all_ings.extend(ings)
        # naive Gruppierung: normalisiere Einheiten minimal
        def normalize(line):
            return re.sub(r"\s+", " ", line.strip())
        grouped = {}
        for line in all_ings:
            k = normalize(line)
            grouped[k] = grouped.get(k, 0) + 1
        st.markdown("**Liste**")
        st.markdown("\n".join([f"- {k}" for k in sorted(grouped.keys())]))
        txt = "\n".join(sorted(grouped.keys()))
        st.download_button("Als TXT speichern", data=txt, file_name="einkaufsliste.txt")
    else:
        st.info("Wähle mindestens ein Rezept.")

st.caption("Lokal, privat, offline‑fähig. Export/Notion‑Sync kann später ergänzt werden.")
