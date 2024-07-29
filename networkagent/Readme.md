# Network Agent

GenAI Agent providing a natural language interface to allow an Enterprise to create, update and view their multi cloud connectivity services. Simplifying the experience of designing and maintaining multi cloud connectivity services. 

The network agent support the following use cases:

* Ask for a description of available connectivity services that can be deployed
* Request a new instance of a connectivity service. Interacting with the Agent to provide the required information to instantiate the chosen service. Confirm all connectivity design decisions and confirm the 
* Update an existing instance of a connectivity service. Interacting with the Agent to ensure all required information is collected and confirming the exection of agreed changes
* View existing services and their configuration
* Request a monitoring service to be deployed for one or more connectivity services
* View monitoring statistics for one or more connectivity services

## Running the Agent on your laptop

```
export KUBECONFIG
export ...
python3 main.py
```

## Running the Agent on GCP

```
docker build -t networkagent .
```

```
docker run networkagent -e
```