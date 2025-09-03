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
import utils.git_helpers as git
import utils.globals as globals

logger = logging.getLogger(__name__)

######################################################################
# Resource to provide the incident operating procedure doc
######################################################################
@globals.networkagent_mcp.tool()
def getIncidentOperatingProcedure()-> str:
    """
    Fetch the current incident method of operations file from git

    Returns:
        incident operations document in markdown format
    """
    logger.info("Getting incident operations guide from git")

    filename = "operating_procedure.md"
    result = git.get_git_file(git.INCIDENT_REPO, filename)
    if result is not None:
        return result
    else:
        logger.error(f"{filename} could not be found")
        return None