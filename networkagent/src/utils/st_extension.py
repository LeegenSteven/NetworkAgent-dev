# A segmented control buttons inspired from 
# Adrian Turiot Maxa at https://github.com/streamlit/streamlit/issues/6004
#
# FIXME: This is to replace temporarily Streamlit st.tabs because there
# was no way (as of Oct 11, 2024) to know which tab is active. This was
# causing trouble to process node selection in the graph displayed in the
# visible tab

import streamlit as st

def segmented_control(labels: list[str], key: str, default: str | None = None, max_size: int = 6) -> str:
    """Group of buttons with the given labels. Return the selected label."""
    if key not in st.session_state:
        st.session_state[key] = default or labels[0]

    selected_label = st.session_state[key]

    def set_label(label: str) -> None:
        st.session_state.update(**{key: label})

    cols = st.columns([1] * len(labels) + [max_size - len(labels)])

    for col, label in zip(cols, labels):
        btn_type = "primary" if selected_label == label else "secondary"
        col.button(label, on_click=set_label, args=(label,), use_container_width=True, type=btn_type)

    return selected_label