# Network Agent

Follow to deploy the gemini network agent. 

## Deploy the Agent

TBD - running locally for now

## Running the Agent from the command line

Install python dependencies

```
pip install -r requirements.txt
streamlit run src/main.py
```

## Running the Agent locally from VSCode

Install python dependencies

```
pip install -r requirements.txt
```

To run the agent in VSCode you can setup a __launch.json__ file as below. 

```
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Python Debugger: Current File",
            "type": "debugpy",
            "request": "launch",
            "module": "streamlit",
            "console": "integratedTerminal",
            "args": [
                "run",
                "${file}"
            ],
            "env": {
                "GOOGLE_PROJECT": "bt-demo-999",
                "GOOGLE_REGION": "europe-west2",
                "GOOGLE_ZONE": "europe-west2-a",
                "NETWORK_AGENT_FILE": "./networkagent.json"
            }
        }
    ]
}
```