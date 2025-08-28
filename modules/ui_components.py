import streamlit as st

def top_nav(items):
    """items: list[(label, key)] returns selected key"""
    if "nav_key" not in st.session_state:
        st.session_state.nav_key = items[0][1]
    cols = st.columns(len(items))
    for i, (label, key) in enumerate(items):
        if cols[i].button(label, use_container_width=True, type=("primary" if st.session_state.nav_key==key else "secondary")):
            st.session_state.nav_key = key
    st.markdown("---")
    return st.session_state.nav_key
