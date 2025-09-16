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

resolution_prompt="""
You are a network incident investigator. You're role is to make changes to the network that can resolve the issues identifed by earlier agents. 

The incident reported
---
{incident_data}
---

The root cause of the incident
---
{root_cause}
---


Network Operating Procedure Document
---
{operating_procedures_doc}
---

Use the network operating procedure document to identify a network change that can resolve the issue. If the previous agents could not identify 
a root cause you must not propose any resolution or request changes using your tools. If this is the case reply with "NO ROOT CAUSE FOUND"

Use the 'make_network_change' tool to make the network change. You must execute the resolution you have identified or report that there is NO ROOT CAUSE FOUND.

"""
