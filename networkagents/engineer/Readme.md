# Network Engineer Agent

Follow the instructions below to deploy and run the network agent backend.

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