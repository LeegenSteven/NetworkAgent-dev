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

import logging
from jinja2 import Environment, FileSystemLoader
import os

logger = logging.getLogger(__name__)


##########################################################
# template the amf manifests
##########################################################
async def template_amf_manifest(folder, filename,address):
    environment = Environment(loader=FileSystemLoader(folder))
    template = environment.get_template(filename)
    output=template.render(
        GOOGLE_REGION=os.getenv("GOOGLE_REGION"),
        GOOGLE_ZONE=os.getenv("GOOGLE_ZONE"),
        GOOGLE_PROJECT=os.getenv("GOOGLE_PROJECT"),
        AMFADDRESS=address
        )
    return output