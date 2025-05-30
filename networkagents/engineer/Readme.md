# Network Engineer Agent

Follow the instructions below to deploy and run the network agent backend.

## Testing with A2A Client

A test script is provided to test the Engineer Agent using an A2A client. This test sends a task to the Engineer Agent and monitors the response.

### Prerequisites

- The Engineer Agent must be running locally or remotely
- Python dependencies must be installed (see below)

### Running the Test

To run the test, use the provided shell script in the test directory:

```bash
cd test
./run_a2a_test.sh
```

You can customize the test with the following options:

```bash
cd test
./run_a2a_test.sh --address http://localhost:8081 --task "Create a mesh network"
```

Options:
- `-a, --address ADDRESS`: Address of the Engineer Agent server (default: http://localhost:8081)
- `-t, --task TASK`: Task to send to the Engineer Agent
- `-d, --data-part`: Send the task as a data part with {'objective': task_text}
- `-h, --help`: Show help message

Example with data part:
```bash
cd test
./run_a2a_test.sh --address http://localhost:8081 --task "Create a mesh network" --data-part
```

### Running Unit Tests

A unittest-based test case is also provided for automated testing. You can use the test runner script:

```bash
# Run all tests
cd test
./run_tests.py

# Run all tests with a specific Engineer Agent address
cd test
./run_tests.py --address http://localhost:8081

# Run specific tests
cd test
./run_tests.py test_send_task test_send_complex_task
./run_tests.py TestEngineerAgent.test_send_task
```

### Test Directory Structure

All test artifacts are located in the `test` directory:

```
networkagents/engineer/test/
├── __init__.py                # Package initialization
├── run_a2a_test.sh            # Script to run the A2A client test
├── run_tests.py               # Python script to run unit tests
├── test_a2a_client.py         # A2A client implementation
└── test_engineer_agent.py     # Unit tests for the Engineer Agent
```

#### Test Files Description

- **test_a2a_client.py**: Implements the A2A client for connecting to the Engineer Agent. It supports sending tasks with both text and data parts. The data part format is `{"objective": "task_text"}`.

- **test_engineer_agent.py**: Contains unit tests for the Engineer Agent. It includes tests for sending tasks with both text and data parts:
  - `test_send_task`: Tests sending a simple task with a text part
  - `test_send_complex_task`: Tests sending a complex task with a text part
  - `test_send_task_with_data_part`: Tests sending a simple task with a data part
  - `test_send_complex_task_with_data_part`: Tests sending a complex task with a data part

- **run_a2a_test.sh**: A shell script for running the A2A client test. It supports options for specifying the address, task, and whether to use a data part.

- **run_tests.py**: A Python script for running the unit tests. It supports running all tests or specific tests.

### Running Tests Directly

You can also run the tests directly:

```bash
# Run the A2A client test directly
cd test
python3 test_a2a_client.py --address http://localhost:8081 --task "Create a network service for connecting two locations"

# Run the A2A client test with data part
cd test
python3 test_a2a_client.py --address http://localhost:8081 --task "Create a network service for connecting two locations" --use-data-part

# Run the unit tests directly
cd test
python3 test_engineer_agent.py

# Run individual test methods
cd test
python3 -m unittest test_engineer_agent.TestEngineerAgent.test_send_task
python3 -m unittest test_engineer_agent.TestEngineerAgent.test_send_complex_task
python3 -m unittest test_engineer_agent.TestEngineerAgent.test_send_task_with_data_part
python3 -m unittest test_engineer_agent.TestEngineerAgent.test_send_complex_task_with_data_part
```

## Deploy the Network Engineer Agent to GCP

The engineer agent is deployed by running the following command.

```
install.sh -n
```

## Running the Network Engineer Agent locally from VSCode

To run the network agent on your local machine, you must first install its python dependencies, i.e.

```
pip install -r networkagent/requirements.txt
```

To run the network agent in VSCode you can setup a __launch.json__ file as below. 

```
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Python Debugger: Current File",
            "type": "debugpy",
            "request": "launch",
            "program": "${file}",
            "env": {
                "BASEDIR": "<YOUR LOCAL DIR>/NetworkAgent/operator/src",
                "GOOGLE_PROJECT": "<YOUR PROJECT>",
                "GOOGLE_REGION": "<YOUR REGION>",
                "GOOGLE_ZONE": "<YOUR ZONE>",
                "ROOT_DIR" : "networkagent/src/",
                "WEBAPPS_LOGIN": "networkagent",
                "WEBAPPS_PWD":"<YOUR PASSWORD>",
                "NETWORK_AGENT_FILE": "./networkagent.json"
            }
        }
    ]
}
```
