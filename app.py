import streamlit as st
from modules.data import init_db
from modules.ui_components import top_nav
from pages.meal_planner import render as render_meal_planner
from pages.cookbook import render as render_cookbook
from pages.shopping_list import render as render_shopping

st.set_page_config(page_title="Meal Planner (Lokal)", layout="wide")
init_db()

page = top_nav([
    ("📅 Meal Planner", "planner"),
    ("📚 Kochbuch", "cookbook"),
    ("🛒 Einkaufsliste", "shopping"),
])

if page == "planner":
    render_meal_planner()
elif page == "cookbook":
    render_cookbook()
else:
    render_shopping()