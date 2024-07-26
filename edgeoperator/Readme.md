# Edge Application Provisioning

Operator that provisions the VyOS docker container in a VM.

## Install Ansible operator sdk

* [Operator SDK](https://sdk.operatorframework.io/)

## Running the operator locallaly

Setup local python environment

```
python3 -m venv venv
source venv/bin/activate
pip3 install ansible ansible-runner ansible-http kubernetes
```

To run the operator locally on your laptop, run the following commands

```
make deploy
make run
```

## Building the docker image


## Deploying to GKE


