# Setup GCP Environment

This section describes how to set up a base Network Agent demo environment. 

## Setup gcloud on your laptop

[Install](https://cloud.google.com/sdk/docs/install) and initialise gcloud as follows:

```
gcloud init --no-launch-browser
```

## Setup GCP environment variables

Setup the following environment variables. They are used throughout the rest of the setup docs.

```
export GOOGLE_PROJECT=<YOUR PROJECT>
export GOOGLE_REGION=<YOUR REGION>
export GOOGLE_ZONE=<YOUR ZONE>
export GOOGLE_USER=<GCE USERNAME> # the name of the deafult user in your virtual machines 
```

## Installation script setup

The __install.sh__ script in the root directory automates setup tasks. The script options are as follows:

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

The first step is to generate the various keys and deployment descriptors for each of the network agent components by running the following commands. 

```
./install.sh -c
```

The run the following command to start the various GCP services, e.g. networks, GKE, network agent operators/tools and sample customer sites. 

```
./install.sh -s
```

