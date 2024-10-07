# Setup GCP Environment

This section describes how to setup the base Network Agent GCP environment.

## Prerequisites

The following packages are required before proceeding with the installation

* [Google Cloud Command Line interface](https://cloud.google.com/sdk/docs/install)
* kubectl (on Debian: ```sudo apt-get install kubectl```)
* Python3 pip installer (on Debian: ```sudo apt-get install python3-pip```)
* jinja templating engine (```pip install jinja-cli```).

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

Setup the following environment variables. They are used throughout the setup docs and installation scripts.

```
export GOOGLE_PROJECT=<YOUR PROJECT>
export GOOGLE_REGION=<YOUR REGION>
export GOOGLE_ZONE=<YOUR ZONE>
export GOOGLE_USER=<GCE USERNAME> # the name of the default Operating System user when you connect to a GCE VM 
```

## Network Agent Installation Script

The __install.sh__ script creates the Network Agent GCP environment in your project.

The script options are as follows:

```
./install.sh
Network Agent environment manager.

Syntax: install.sh [-c|-s|-o|-r|-n|-k|-d]
options:
  -c     create keys and manifests.
  -s     start network agent environment.
  -o     build and deploy the operator
  -r     build and deploy the rest tools
  -n     build and deploy the networkagent
  -k     kill the environment resources.
  -d     delete the network agent environment.
```

The first step is to generate keys and deployment descriptors for each of the network agent GCP components by running the following command. 

```
./install.sh -c
```

Then run the command below to start the GCP services, e.g. VPCs, GKE Cluster, Network Agent K8s operastor, Git repos, network AI agent etc. 

```
./install.sh -s
```

