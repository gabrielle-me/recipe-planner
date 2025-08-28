import uuid
import streamlit as st
from modules.data import list_recipes, get_recipe, insert_recipe, insert_ingredient, insert_step, now_iso
from modules.importer import import_from_url, import_from_pdf, import_from_image

from modules.importer import clean_lines, guess_sections

import pdfplumber
import pytesseract
from PIL import Image

import io

from sqlalchemy import text
from modules.data import engine  # DB-Engine für Updates/Deletes wiederverwenden
import uuid as _uuid


def render():
    st.header("Kochbuch")
    tabs = st.tabs(["+ Import", "📚 Rezepte"])

    with tabs[0]:
        st.subheader("Rezept hinzufügen")
        src = st.radio("Quelle", ["URL", "Text", "PDF", "Bild"], horizontal=True)

        if src == "URL":
            url = st.text_input("Rezept‑URL")
            if st.button("Importieren", disabled=not url):
                try:
                    data, raw = import_from_url(url)
                    rid = str(uuid.uuid4())
                    insert_recipe({
                        "id": rid,
                        "title": data.get("title") or "Unbenanntes Rezept",
                        "source_url": url,
                        "source_type": "url",
                        "servings": data.get("servings") or "2 Portionen",
                        "total_time": data.get("total_time"),
                        "image_url": data.get("image_url"),
                        "raw_text": raw,
                        "created_at": now_iso(),
                    })
                    for line in data.get("ingredients") or []:
                        insert_ingredient({"id": str(uuid.uuid4()), "recipe_id": rid, "line": line})
                    for idx, step in enumerate(data.get("steps") or []):
                        insert_step({"id": str(uuid.uuid4()), "recipe_id": rid, "idx": idx, "instruction": step})
                    st.success("Gespeichert.")
                except Exception as e:
                    st.error(f"Fehler: {e}")

        elif src == "Text":
            txt = st.text_area("Rezept‑Text", height=200, placeholder="Titel optional in erster Zeile, dann Zutaten / Zubereitung…")
            if st.button("Importieren", disabled=not txt.strip()):
                lines = clean_lines(txt)
                ingredients, steps = guess_sections(lines)
                title = lines[0][:120] if lines else "Freitext Rezept"
                rid = str(uuid.uuid4())
                insert_recipe({
                    "id": rid,
                    "title": title,
                    "source_url": None,
                    "source_type": "text",
                    "servings": "2 Portionen",
                    "total_time": None,
                    "image_url": None,
                    "raw_text": txt,
                    "created_at": now_iso(),
                })
                for line in ingredients:
                    insert_ingredient({"id": str(uuid.uuid4()), "recipe_id": rid, "line": line})
                for idx, step in enumerate(steps):
                    insert_step({"id": str(uuid.uuid4()), "recipe_id": rid, "idx": idx, "instruction": step})
                st.success("Gespeichert.")

        elif src == "PDF":
            up = st.file_uploader("PDF hochladen", type=["pdf"])
            if up and st.button("Importieren"):
                from modules.importer import import_from_pdf
                data, raw = import_from_pdf(up.read())
                rid = str(uuid.uuid4())
                insert_recipe({
                    "id": rid,
                    "title": data.get("title") or "PDF Rezept",
                    "source_url": None,
                    "source_type": "pdf",
                    "servings": data.get("servings") or "2 Portionen",
                    "total_time": data.get("total_time"),
                    "image_url": None,
                    "raw_text": raw,
                    "created_at": now_iso(),
                })
                for line in data.get("ingredients") or []:
                    insert_ingredient({"id": str(uuid.uuid4()), "recipe_id": rid, "line": line})
                for idx, step in enumerate(data.get("steps") or []):
                    insert_step({"id": str(uuid.uuid4()), "recipe_id": rid, "idx": idx, "instruction": step})
                st.success("Gespeichert.")

        elif src == "Bild":
            ups = st.file_uploader("Screenshots/Bilder (mehrere möglich)", type=["png","jpg","jpeg","webp"], accept_multiple_files=True)
            if ups and st.button("Importieren (OCR)"):
                texts = []
                for up in ups:
                    img = Image.open(up).convert("RGB")
                    texts.append(pytesseract.image_to_string(img, lang="deu+eng"))
                combined_text = "\n".join(texts)
                lines = clean_lines(combined_text)
                ingredients, steps = guess_sections(lines)
                title = lines[0][:120] if lines else "OCR Rezept"
                rid = str(uuid.uuid4())
                insert_recipe({
                    "id": rid,
                    "title": title,
                    "source_url": None,
                    "source_type": "image",
                    "servings": "2 Portionen",
                    "total_time": None,
                    "image_url": None,
                    "raw_text": combined_text,
                    "created_at": now_iso(),
                })
                for line in ingredients:
                    insert_ingredient({"id": str(uuid.uuid4()), "recipe_id": rid, "line": line})
                for idx, step in enumerate(steps):
                    insert_step({"id": str(uuid.uuid4()), "recipe_id": rid, "idx": idx, "instruction": step})
                st.success("Gespeichert.")

    with tabs[1]:
        s = st.text_input("Suche nach Titel")
        rows = list_recipes(s)
        import pandas as pd
        df = pd.DataFrame(rows, columns=["id","Titel","Portionen","Zeit","Bild","Erstellt"])
        st.dataframe(df.drop(columns=["id"]))
        selected = st.selectbox("Rezept öffnen", options=["-"] + [r[0] for r in rows], format_func=lambda x: "-" if x=="-" else next((r[1] for r in rows if r[0]==x), x))
        if selected and selected != "-":
            r, ings, steps = get_recipe(selected)
            st.markdown(f"### {r.title}")
            meta = []
            if r.servings: meta.append(f"Portionen: {r.servings}")
            if r.total_time: meta.append(f"Zeit: {r.total_time}")
            if meta:
                st.caption(" · ".join(meta))
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Zutaten**")
                st.markdown("\n".join([f"- {i}" for i in ings]) or "—")
            with c2:
                st.markdown("**Zubereitung**")
                st.markdown("\n".join([f"{idx+1}. {s}" for idx, s in enumerate(steps)]) or "—")
            # --- 📝 Bearbeiten / 🗑️ Löschen ---
            with st.expander("📝 Bearbeiten / 🗑️ Löschen", expanded=False):
                new_title = st.text_input("Titel", value=r.title or "")
                new_servings = st.text_input("Portionen", value=r.servings or "")
                new_time = st.text_input("Zeit", value=r.total_time or "")
                ing_text = st.text_area("Zutaten (eine pro Zeile)", value="\n".join(ings), height=180)
                step_text = st.text_area("Schritte (eine pro Zeile)", value="\n".join(steps), height=220)

                col_save, col_delete = st.columns(2)

                with col_save:
                    if st.button("💾 Änderungen speichern", type="primary"):
                        try:
                            new_ings = [l.strip() for l in ing_text.splitlines() if l.strip()]
                            new_steps = [l.strip() for l in step_text.splitlines() if l.strip()]
                            with engine.begin() as conn:
                                # Rezept-Stammdaten updaten
                                conn.execute(
                                    text("UPDATE recipes SET title=:t, servings=:s, total_time=:tt WHERE id=:id"),
                                    {"t": new_title, "s": new_servings, "tt": new_time, "id": selected},
                                )
                                # Zutaten/Schritte ersetzen (löschen + neu einfügen)
                                conn.execute(text("DELETE FROM ingredients WHERE recipe_id=:id"), {"id": selected})
                                conn.execute(text("DELETE FROM steps WHERE recipe_id=:id"), {"id": selected})
                                for line in new_ings:
                                    conn.execute(
                                        text("INSERT INTO ingredients(id,recipe_id,line) VALUES(:id,:rid,:line)"),
                                        {"id": str(_uuid.uuid4()), "rid": selected, "line": line},
                                    )
                                for idx2, inst in enumerate(new_steps):
                                    conn.execute(
                                        text("INSERT INTO steps(id,recipe_id,idx,instruction) VALUES(:id,:rid,:idx,:ins)"),
                                        {"id": str(_uuid.uuid4()), "rid": selected, "idx": idx2, "ins": inst},
                                    )
                            st.success("Rezept aktualisiert.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Fehler beim Speichern: {e}")

                with col_delete:
                    st.markdown("**Gefährlicher Bereich**")
                    confirm = st.checkbox("Ich möchte dieses Rezept endgültig löschen.")
                    if st.button("🗑️ Endgültig löschen", disabled=not confirm):
                        try:
                            with engine.begin() as conn:
                                # Sicher löschen (falls ON DELETE CASCADE nicht aktiv ist)
                                conn.execute(text("DELETE FROM ingredients WHERE recipe_id=:id"), {"id": selected})
                                conn.execute(text("DELETE FROM steps WHERE recipe_id=:id"), {"id": selected})
                                conn.execute(text("DELETE FROM recipes WHERE id=:id"), {"id": selected})
                            st.success("Rezept gelöscht.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Fehler beim Löschen: {e}")


