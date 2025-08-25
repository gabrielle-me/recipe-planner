# recipe-planner

Private Streamlit-App zum Importieren von Rezepten aus URL, Text, PDF und Screenshots (OCR).
- Speichert lokal in SQLite (`data/recipes.db`)
- Einkaufsliste aus ausgewählten Rezepten
- Deutsche UI

## Quickstart
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
streamlit run app.py
