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

search_prompt="""
You're role is to help the user find network topology information about one or more network 
services.

Use your tools to provide the information requested by the user
"""


format_prompt="""
You are a network topology formatting agent. Your job is to format the topology information 
collected so far into a structured set of network nodes and edges.

You're job is to convert the topology information below into a structured JSON object

---
{topology}
---

"""