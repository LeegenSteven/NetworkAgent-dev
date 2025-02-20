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