import io
import uuid
import requests
import pdfplumber
import pytesseract
from PIL import Image
from bs4 import BeautifulSoup
from w3lib.html import get_base_url
import extruct
import trafilatura
from readability import Document
import re
from modules.data import insert_recipe, insert_ingredient, insert_step, now_iso

RE_ING_HEADER = re.compile(r"^(zutaten|ingredients)\b", re.I)
RE_STEPS_HEADER = re.compile(r"^(zubereitung|anleitung|directions|method)\b", re.I)
BULLET = re.compile(r"^\s*([\-•\*\u2022\u2023\u2043]|\d+\.)\s+")

def clean_lines(text: str):
    lines = [re.sub(r"\s+", " ", l.strip()) for l in text.splitlines()]
    return [l for l in lines if l]

def guess_sections(lines):
    ingredients, steps = [], []
    mode = None
    for l in lines:
        if RE_ING_HEADER.search(l):
            mode = "ing"; continue
        if RE_STEPS_HEADER.search(l):
            mode = "steps"; continue
        if mode == "ing":
            if BULLET.search(l) or re.match(r"^\d*\s?[a-zA-ZäöüÄÖÜ]+", l):
                ingredients.append(BULLET.sub("", l))
            else:
                if l and len(l.split()) > 3:
                    mode = "steps"
        if mode == "steps":
            if l:
                steps.append(BULLET.sub("", l))
    if not ingredients:
        ing_block = []
        for l in lines:
            if BULLET.search(l):
                ing_block.append(BULLET.sub("", l))
            elif ing_block:
                break
        if ing_block:
            ingredients = ing_block
            idx = lines.index(ing_block[-1]) if ing_block[-1] in lines else 0
            steps = [l for l in lines[idx+1:] if l]
    return ingredients, steps

def extract_from_jsonld(data):
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

def import_from_url(url: str):
    resp = requests.get(url, timeout=20)
    resp.raise_for_status(); html = resp.text
    base = get_base_url(html, url)
    data = extruct.extract(html, base_url=base, syntaxes=["json-ld", "microdata", "opengraph"]) 
    for blob in data.get("json-ld", []) + data.get("microdata", []):
        node = blob.get("@graph") if isinstance(blob.get("@graph"), list) else [blob]
        for item in node:
            t = item.get("@type"); types = t if isinstance(t, list) else [t]
            if any(x in ("Recipe",) for x in types):
                parsed = extract_from_jsonld(item)
                if parsed.get("title"):
                    return parsed, html
    try:
        readable = Document(html).summary(html_partial=True)
        soup = BeautifulSoup(readable, "lxml"); title = Document(html).short_title()
    except Exception:
        soup = BeautifulSoup(html, "lxml"); title = soup.title.string.strip() if soup.title else None
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

def import_from_pdf(file_bytes: bytes):
    text_parts = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""; text_parts.append(txt)
    text = "\n".join(text_parts)
    lines = clean_lines(text)
    ingredients, steps = guess_sections(lines)
    title = lines[0][:120] if lines else None
    return {
        "title": title,
        "image_url": None,
        "servings": None,
        "total_time": None,
        "ingredients": ingredients,
        "steps": steps,
    }, text

def import_from_image(file_bytes: bytes):
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
