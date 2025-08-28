from datetime import date, timedelta
import random
import streamlit as st
from modules.data import week_dates, monday_of_week, MEAL_SLOTS, list_recipes, get_plan_items_for_week, add_plan_item, remove_plan_item, update_plan_item_servings, get_recipe

LABELS = dict(MEAL_SLOTS)

_DEF_SERVINGS = 2

@st.cache_data(show_spinner=False)
def _recipe_index(search: str | None = None):
    rows = list_recipes(search)
    # return list of (id, title)
    return [(r[0], r[1]) for r in rows]

def _week_nav():
    today = date.today()
    if "planner_week" not in st.session_state:
        st.session_state.planner_week = monday_of_week(today)
    c1, c2, c3, c4 = st.columns([1,1,2,1])
    if c1.button("◀︎ Woche"):
        st.session_state.planner_week -= timedelta(days=7)
    if c2.button("Diese Woche"):
        st.session_state.planner_week = monday_of_week(today)
    if c4.button("Woche ▶︎"):
        st.session_state.planner_week += timedelta(days=7)
    wk = week_dates(st.session_state.planner_week)
    c3.markdown(f"**{wk[0].strftime('%d.%m.%Y')} – {wk[-1].strftime('%d.%m.%Y')}**")
    return wk

def _slot_key(day: date, slot_key: str):
    return f"{day.isoformat()}::{slot_key}"

def _servings_input(default: int):
    return st.number_input("Portionen", min_value=1, value=int(default), step=1, key=str(random.random()))

def render():
    st.header("Meal Planner")
    wk = _week_nav()

    # Swipe Deck (rechts)
    left, right = st.columns([2,1])

    with right:
        st.subheader("Rezepte swipen")
        search = st.text_input("Suche im Kochbuch")
        idx = _recipe_index(search)
        if not idx:
            st.info("Keine Rezepte gefunden. Bitte im Kochbuch importieren.")
        else:
            if "deck_pos" not in st.session_state:
                st.session_state.deck_pos = 0
            if st.button("🔀 Mischen"):
                random.shuffle(idx)
                st.session_state.deck_pos = 0
            st.caption(f"{st.session_state.deck_pos+1}/{len(idx)}")
            rid, title = idx[st.session_state.deck_pos]
            st.markdown(f"**{title}**")
            # Ziel auswählen
            day = st.selectbox("Tag", wk, format_func=lambda d: d.strftime("%a %d.%m."))
            slot_key = st.selectbox("Slot", [k for k,_ in MEAL_SLOTS], format_func=lambda k: LABELS[k])
            base_serv = 2
            r, _, _ = get_recipe(rid)
            try:
                from modules.scaling import extract_servings_num
                base_serv = int(extract_servings_num(r.servings) or _DEF_SERVINGS)
            except Exception:
                base_serv = _DEF_SERVINGS
            target_serv = st.number_input("Portionen", min_value=1, value=int(base_serv), step=1)
            c1, c2 = st.columns(2)
            if c1.button("➡️ In Plan übernehmen"):
                add_plan_item(str(day), slot_key, rid, int(target_serv))
                st.success("Hinzugefügt.")
            if c2.button("⏭️ Überspringen"):
                st.session_state.deck_pos = (st.session_state.deck_pos + 1) % len(idx)

    with left:
        st.subheader("Wochenübersicht")
        rows = get_plan_items_for_week(wk[0])
        grid = {d: {k: [] for k,_ in MEAL_SLOTS} for d in wk}
        for it in rows:
            grid[date.fromisoformat(it.plan_date)][it.meal_slot].append(it)
        for d in wk:
            st.markdown(f"### {d.strftime('%A, %d.%m.')} ")
            cols = st.columns(3)
            for i, (slot_key, slot_label) in enumerate(MEAL_SLOTS):
                with cols[i]:
                    st.markdown(f"**{slot_label}**")
                    items = grid[d][slot_key]
                    if not items:
                        st.caption("— leer —")
                    for it in items:
                        r, _, _ = get_recipe(it.recipe_id)
                        st.markdown(f"- {r.title}")
                        new_serv = st.number_input("Portionen", min_value=1, value=int(it.servings_target or _DEF_SERVINGS), step=1, key=f"srv-{it.id}")
                        if new_serv != it.servings_target:
                            update_plan_item_servings(it.id, int(new_serv))
                            st.toast("Portionen aktualisiert")
                        if st.button("Entfernen", key=f"del-{it.id}"):
                            remove_plan_item(it.id)
                            st.toast("Entfernt")
