from datetime import date
import streamlit as st
from modules.data import shopping_list_for_week, monday_of_week


def render():
    st.header("Einkaufsliste")
    wk = monday_of_week(date.today())
    st.caption("Liste basiert auf der aktuellen Wochenplanung (Mo–So).")
    items = shopping_list_for_week(wk)
    if not items:
        st.info("Noch keine Rezepte in der Woche geplant.")
        return
    st.markdown("**Liste**")
    st.markdown("\n".join([f"- {k}" for k in items]))
    txt = "\n".join(items)
    st.download_button("Als TXT speichern", data=txt, file_name="einkaufsliste.txt")
