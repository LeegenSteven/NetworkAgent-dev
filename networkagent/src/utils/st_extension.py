# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# A segmented control buttons inspired from 
# Adrian Turiot Maxa at https://github.com/streamlit/streamlit/issues/6004
#
# FIXME: This is to replace temporarily Streamlit st.tabs because there
# was no way (as of Oct 11, 2024) to know which tab is active. This was
# causing trouble to process node selection in the graph displayed in the
# visible tab

import streamlit as st

def segmented_control(labels: list[str], key: str, default: str | None = None, max_size: int = 6) -> str:
    """Group of buttons with the given labels styled in Google Cloud design. Return the selected label."""
    if key not in st.session_state:
        st.session_state[key] = default or labels[0]

    selected_label = st.session_state[key]

    def set_label(label: str) -> None:
        st.session_state.update(**{key: label})

    # Add Google Cloud style to buttons
    st.markdown("""
    <style>
    /* Google Cloud style for button containers */
    div[data-testid="column"] > div[data-testid="stButton"] > button {
        background-color: #F1F3F4;
        color: #5F6368;
        border: none;
        border-radius: 18px;
        font-family: 'Google Sans', sans-serif;
        font-weight: 500;
        padding: 4px 16px;
        transition: all 0.2s ease;
    }
    
    /* Active button style */
    div[data-testid="column"] > div[data-testid="stButton"] > button[kind="primary"] {
        background-color: white !important;
        color: #4285F4 !important;
        box-shadow: 0 1px 2px rgba(60, 64, 67, 0.3);
    }
    
    /* Button container styling for segmented control */
    div.row-widget.stButton {
        background-color: #F1F3F4;
        border-radius: 20px;
        padding: 3px;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # Use columns for the buttons
    cols = st.columns([1] * len(labels) + [max_size - len(labels)])
    
    # Display buttons with GCP style
    for col, label in zip(cols, labels):
        btn_type = "primary" if selected_label == label else "secondary"
        col.button(label, on_click=set_label, args=(label,), use_container_width=True, type=btn_type)
    
    return selected_label
