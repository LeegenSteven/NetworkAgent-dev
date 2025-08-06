# Setup GCP Environment

This section describes how to setup the base Network Agent GCP environment.

## Prerequisites

The following packages are required before proceeding with the installation

* [Google Cloud Command Line interface](https://cloud.google.com/sdk/docs/install)
* kubectl (on Debian: ```sudo apt-get install kubectl```)
* Python3 pip installer (on Debian: ```sudo apt-get install python3-pip```)
* jinja templating engine (```pip install jinja-cli```).
* ansible (```pip install ansible```).

Note: it is recommended to create your own Python virtual environment first prior to installing jinja or any other python packages.

### Update Organization Policies

Ensure the organization policy values below are set as follows. 

* Set __constraints/compute.vmExternalIpAccess__ to __Allow All__
* Set __constraints/compute.requireShieldedVm__ to __Off__
* Set __constraints/iam.disableServiceAccountKeyCreation__ to __Off__
* Set __constraints/compute.vmCanIpForward__ to __Allow All__
* Set __constraints/iam.allowedPolicyMemberDomains__ to __Allow All__

## Setup gcloud

[Install](https://cloud.google.com/sdk/docs/install) and initialise gcloud:

```
gcloud init --no-launch-browser
```

## Setup GCP environment variables

Setup and export the following environment variables. They are used throughout the setup docs and installation scripts.

```
  export GOOGLE_PROJECT=<YOUR PROJECT>  # the GCP project name hosting the NW Agent demo (You MUST create it first on GCP)
  export GOOGLE_USER=<GCP_USERNAME>  # the user you authenticate with on GCP. It MUST be the owner of the GOOGLE_PROJECT (e.g. john.doe@mydomain.com)
  export GOOGLE_VM_USER=<GCE_VM_USERNAME>  # the default user name on GCE VMs (usually john_doe_mydomain_com but to be sure create a VM, SSH connect from the web console, type whoami', delete VM)
  export GOOGLE_REGION=<YOUR_REGION>  # the GCP region to host the demo environment (e.g. europe-west1)
  export GOOGLE_ZONE=<YOUR_ZONE>  # the GCP zone in the region to host the demo environment (e.g.europe-west1-c)
  export WEBAPPS_LOGIN=<YOUR_WEB_LOGIN>  # the login name to access web apps like the NW Agent UI or the Gitops Web UI"
  export WEBAPPS_PWD=<YOUR_WEB_PWD>  # the password to access the web apps

```

## Network Agent Installation Script

The __install.sh__ script creates the Network Agent GCP environment in your project.

The script options are as follows:

```shell
./install.sh
Network Agent environment manager.

Syntax: install.sh [-c|-s|-b|-o|-l|-r|-n|-k|-d|-p|-g|-i]
options:
  -c     create network agent environment (keys, manifests,..)
  -s     build and start network agent runtime (incl. the operator)
  -b     build the Virtual Network Function image with Free5GC, UERANSIM, Docker, and Wireguard
  -o     build and deploy the network operator
  -l     build and deploy the logs capture function
  -n     build and deploy the network dashboard and network agents
         can be followed by a comma-separated list of agent names to (re)deploy selectively
         valid agent names: all, networktools, supervisor, engineer, dashboard, operations, test
         example: -n dashboard,operations or -n all (to deploy all agents)
  -k     stop and delete the network agent runtime (GKE cluster, VMS, DB, etc..)
  -d     delete the network agent environment (keys, manifests...).
  -p     deploy porch tools
  -i     display demo information
  -g     display active GCP environment (user, project, GKE cluster,...)
  -i     display demo information

Some typical use cases:
 - To create and run a network agent environment including the operator: ./install.sh -c; ./install.sh -s
 - To redeploy the operator alone : ./install.sh -o
 - To (re)deploy the network agent Web UI alone : ./install.sh -n
 - To regenerate the network agent runtime with the same environment setup: ./install.sh -k; ./install.sh -s
 - To recreate a complete environment and runtime from scratch: ./install.sh -k; ./install.sh -d; ./install.sh -c; ./install.sh -s
```

The first step is to generate keys and deployment descriptors for each of the network agent GCP components by running the following command. 

```shell
./install.sh -c
```

Then run the command below to start the GCP services, e.g. VPCs, GKE Cluster, Network Agent K8s operastor, Git repos, network AI agent etc. 

```shell
./install.sh -s
```

To build the free5gc network virtual machine (this needs to be done only once).

```shell
./install.sh -b
```

To deploy the Network Agent UI in Cloud Run (recommended)

```shell
./install.sh -n all
```
Alternatively you may install the Network Agent on your own machine

```shell
pip install -r ./networkagent/requirements.txt
(cd ./networkagent/src; streamlit run ./main.py)
```
To recreate a complete environment and runtime from scratch: 
```shell
./install.sh -k; ./install.sh -d; ./install.sh -c; ./install.sh -s
```