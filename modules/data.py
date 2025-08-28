import os
import time
from datetime import date, timedelta
from typing import List, Tuple, Optional
from sqlalchemy import create_engine, text

DB_PATH = os.path.join("data", "recipes.db")
os.makedirs("data", exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}", future=True)

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
CREATE TABLE IF NOT EXISTS plan_items (
    id TEXT PRIMARY KEY,
    plan_date TEXT,          -- YYYY-MM-DD
    meal_slot TEXT,          -- breakfast|lunch|dinner
    recipe_id TEXT,
    servings_target INTEGER,
    created_at TEXT,
    FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
);
"""

def init_db():
    with engine.begin() as conn:
        for stmt in SCHEMA_SQL.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))

# ---- helpers ----

def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")

MEAL_SLOTS = [
    ("breakfast", "Frühstück"),
    ("lunch", "Mittag"),
    ("dinner", "Abend"),
]

# ---- recipes ----

def insert_recipe(rec):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO recipes(id,title,source_url,source_type,servings,total_time,image_url,raw_text,created_at)
            VALUES(:id,:title,:source_url,:source_type,:servings,:total_time,:image_url,:raw_text,:created_at)
        """), rec)

def insert_ingredient(row):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO ingredients(id,recipe_id,line) VALUES(:id,:recipe_id,:line)"), row)

def insert_step(row):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO steps(id,recipe_id,idx,instruction) VALUES(:id,:recipe_id,:idx,:instruction)"), row)

def list_recipes(search: Optional[str] = None):
    q = "SELECT id,title,servings,total_time,image_url,created_at FROM recipes"
    params = {}
    if search:
        q += " WHERE title LIKE :q"
        params["q"] = f"%{search}%"
    q += " ORDER BY created_at DESC"
    with engine.begin() as conn:
        return conn.execute(text(q), params).fetchall()

def get_recipe(recipe_id: str):
    with engine.begin() as conn:
        r = conn.execute(text("SELECT * FROM recipes WHERE id=:id"), {"id": recipe_id}).fetchone()
        ings = conn.execute(text("SELECT line FROM ingredients WHERE recipe_id=:id"), {"id": recipe_id}).fetchall()
        steps = conn.execute(text("SELECT idx,instruction FROM steps WHERE recipe_id=:id ORDER BY idx"), {"id": recipe_id}).fetchall()
    return r, [i[0] for i in ings], [s[1] for s in steps]

# ---- planner ----

def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())

def week_dates(d: date):
    m = monday_of_week(d)
    return [m + timedelta(days=i) for i in range(7)]

def get_plan_items_for_week(d: date):
    days = week_dates(d)
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT * FROM plan_items WHERE plan_date BETWEEN :a AND :b ORDER BY plan_date, meal_slot, created_at"
        ), {"a": str(days[0]), "b": str(days[-1])}).fetchall()
    return rows

def add_plan_item(plan_date: str, meal_slot: str, recipe_id: str, servings_target: int):
    import uuid
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO plan_items(id,plan_date,meal_slot,recipe_id,servings_target,created_at)
            VALUES(:id,:plan_date,:meal_slot,:recipe_id,:servings_target,:created_at)
        """), {
            "id": str(uuid.uuid4()),
            "plan_date": plan_date,
            "meal_slot": meal_slot,
            "recipe_id": recipe_id,
            "servings_target": servings_target,
            "created_at": now_iso(),
        })

def remove_plan_item(item_id: str):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM plan_items WHERE id=:id"), {"id": item_id})

def update_plan_item_servings(item_id: str, servings_target: int):
    with engine.begin() as conn:
        conn.execute(text("UPDATE plan_items SET servings_target=:s WHERE id=:id"), {"s": servings_target, "id": item_id})

# ---- shopping list aggregation ----
from modules.scaling import extract_servings_num, scale_line_smart

def shopping_list_for_week(d: date):
    """Aggregiert Zutaten aus geplanter Woche; skaliert je Slot‑Portionen."""
    rows = get_plan_items_for_week(d)
    items = []
    for it in rows:
        r, ings, _ = get_recipe(it.recipe_id)
        base = extract_servings_num(r.servings) or 2.0
        factor = (it.servings_target or base) / base
        for line in ings:
            items.append(scale_line_smart(line, factor))
    # naive grouping
    norm = lambda s: " ".join(str(s).split())
    grouped = {}
    for line in items:
        k = norm(line)
        grouped[k] = grouped.get(k, 0) + 1
    return [k for k in sorted(grouped.keys())]
