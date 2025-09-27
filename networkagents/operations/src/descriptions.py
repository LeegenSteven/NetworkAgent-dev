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

description="""
An operations agent. The operations agent job is to communicate with the user to help them understand and 
assess what network services are available to be deployed and what network services or network locations are already deployed.  

The operations agent can help the user fulfill tasks such as:
- which network services are available to use
- which network services are deployed already
- which networking locations are available
- the state of the network resources deployed and their configuration
- find out how network services are connected to each other via one or more network locations
- query performance metrics for network services
"""

tags=['chat']

examples=[
    "What network locations are there?",
    "What network services are deployed?",
    "What network services can i deploy?",
    "Give me more information on how to deploy a UPF",
    "What rules do i need to be aware of when deploying a UPF?",
    "What is the path between ueransim1 and dnn?",
    "Fetch the performance metrics for ueransim1 and dnn in the past 10 mins"
]
