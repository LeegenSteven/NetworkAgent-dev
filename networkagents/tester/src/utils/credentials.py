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
import google.auth
import os
from utils.error_handler import (
    TestAgentError,
    AuthenticationError,
    ErrorSeverity
)

logger = logging.getLogger(__name__)

# global credentials
credentials = None
def get_credentials():
    """
    Get a cached credentials object. Used to auth with spanner
    Returns:
      google auth object
    
    Raises:
      AuthenticationError: If credentials cannot be loaded
    """
    global credentials
    if credentials is None:
        try:
            credentials_file = os.getenv("NETWORK_AGENT_FILE", "/agent/networkagent.json")
            logger.info(f"Loading credentials from {credentials_file}")
            
            if not os.path.exists(credentials_file):
                raise AuthenticationError(
                    message=f"Credentials file not found: {credentials_file}",
                    severity=ErrorSeverity.ERROR,
                    details={"file_path": credentials_file}
                )
                
            credentials = google.auth.load_credentials_from_file(credentials_file)[0]
            logger.info("Successfully loaded credentials")
        except AuthenticationError:
            # Re-raise AuthenticationError instances
            raise
        except Exception as e:
            logger.error(f"Error loading credentials: {str(e)}", exc_info=True)
            raise AuthenticationError(
                message=f"Failed to load credentials: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
    return credentials
