#!/bin/bash
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

# Default values
ORDER_ADDRESS="http://localhost:8089"

# Help message
function show_help {
    echo "Usage: $0 [options]"
    echo "Options:"
    echo "  -a, --address ADDRESS  Address of the Order Agent server (default: $ORDER_ADDRESS)"
    echo "  -h, --help             Show this help message"
    echo ""
    echo "Example:"
    echo "  $0 --address http://localhost:8089"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        -a|--address)
            ENGINEER_ADDRESS="$2"
            shift
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Check if the Order Agent is running
echo "Checking if Order Agent is running at $ORDER_ADDRESS..."
if ! curl -s --head "$ORDER_ADDRESS" > /dev/null; then
    echo "Error: Engineer Agent is not running at $ORDER_ADDRESS"
    echo "Please start the Order Agent before running this test."
    exit 1
fi

# Get the directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Run the test
echo "Running A2A client test with task: '$TASK'"
echo "Using data part with {'objective': '$TASK'}"
python3 "$SCRIPT_DIR/test_a2a_client.py" --address "$ORDER_ADDRESS"

# Check the exit code
if [ $? -eq 0 ]; then
    echo "Test completed successfully!"
else
    echo "Test failed!"
fi
