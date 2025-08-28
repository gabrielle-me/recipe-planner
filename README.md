# Choosy‑like Meal Planner – Local App (Streamlit)

**Ziele**
- Wochenplaner mit "Swipe"‑ähnlicher Rezeptauswahl
- Kochbuch (Import: URL, Text, PDF, Bilder/Screenshots mit OCR)
- Einkaufsliste (aus Wochenplan aggregiert)
- Portionen anpassen pro Plan‑Slot (skaliert Zutaten automatisch)
- Lokal, offline, SQLite


## Installation
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```
> Für OCR bitte Tesseract installieren (System):
> - macOS: `brew install tesseract`
> - Ubuntu: `sudo apt-get install tesseract-ocr`
> - Windows: Installer von tesseract-ocr.

## Starten
```bash
streamlit run app.py
```

## Projektstruktur
```
app.py
requirements.txt
.data/ (wird automatisch erstellt)
pages/__init__.py
pages/meal_planner.py
pages/cookbook.py
pages/shopping_list.py
modules/data.py
modules/importer.py
modules/scaling.py
modules/ui_components.py
```