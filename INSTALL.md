# Network Agent Installation Guide

This guide describes how to set up the Network Agent GCP environment.

## Quick Start

For a complete installation from scratch, simply run:

```bash
./install.sh -all
```

This command will automatically:
- Create the environment configuration if needed
- Build the Virtual Network Function image if needed  
- Start the runtime services
- Deploy all network agents and dashboard

## Prerequisites

The following packages are required before proceeding with the installation:

* [Google Cloud Command Line interface](https://cloud.google.com/sdk/docs/install)
* kubectl (on Debian: `sudo apt-get install kubectl`)
* Python3 pip installer (on Debian: `sudo apt-get install python3-pip`)
* jinja templating engine (`pip install jinja-cli`)
* ansible (`pip install ansible`)
* [flutter sdk](https://flutter.dev/)

**Note:** It is recommended to create your own Python virtual environment first prior to installing jinja or any other python packages.

### Update Organization Policies

Ensure the organization policy values below are set as follows:

* Set **constraints/compute.vmExternalIpAccess** to **Allow All**
* Set **constraints/compute.requireShieldedVm** to **Off**
* Set **constraints/iam.disableServiceAccountKeyCreation** to **Off**
* Set **constraints/compute.vmCanIpForward** to **Allow All**
* Set **constraints/iam.allowedPolicyMemberDomains** to **Allow All**

## Environment Setup

### Setup gcloud

[Install](https://cloud.google.com/sdk/docs/install) and initialize gcloud:

```bash
gcloud init --no-launch-browser
```

### Setup GCP Environment Variables

Setup and export the following environment variables. They are used throughout the setup docs and installation scripts:

```bash
export GOOGLE_PROJECT=<YOUR PROJECT>        # the GCP project name hosting the NW Agent demo (You MUST create it first on GCP)
export GOOGLE_USER=<GCP_USERNAME>           # the user you authenticate with on GCP. It MUST be the owner of the GOOGLE_PROJECT (e.g. john.doe@mydomain.com)
export GOOGLE_VM_USER=<GCE_VM_USERNAME>     # the default user name on GCE VMs (usually john_doe_mydomain_com but to be sure create a VM, SSH connect from the web console, type 'whoami', delete VM)
export GOOGLE_REGION=<YOUR_REGION>          # the GCP region to host the demo environment (e.g. europe-west1)
export GOOGLE_ZONE=<YOUR_ZONE>              # the GCP zone in the region to host the demo environment (e.g. europe-west1-c)
export WEBAPPS_LOGIN=<YOUR_WEB_LOGIN>       # the login name to access web apps like the NW Agent UI or the Gitops Web UI
export WEBAPPS_PWD=<YOUR_WEB_PWD>           # the password to access the web apps
```

## Installation Options

The **install.sh** script provides flexible installation options:

```bash
Network Agent environment manager.

Syntax: install.sh [-c|-s|-b|-o|-l|-r|-n|-k|-d|-g|-i|-all] [-y|-N]
options:
  -all   install everything (comprehensive setup: create env if needed, build image if needed, start runtime, deploy all agents)
         can be combined with -y or -N flags (e.g., ./install.sh -all -y)
  -c     create network agent environment (keys, manifests,..)
  -s     build and start network agent runtime (incl. the operator)
  -b     build the Virtual Network Function image with Free5GC, UERANSIM, Docker, and Wireguard
  -o     build and deploy the network operator
  -l     build and deploy the logs capture function
  -n     build and deploy the network dashboard and network agents
         can be followed by a comma-separated list of agent names to (re)deploy selectively
         valid agent names: all, networktools, supervisor, engineer, dashboard, operations, test, incident, logs
         example: -n dashboard,operations or -n all (to deploy all agents)
  -k     stop and delete the network agent runtime (GKE cluster, VMS, DB, etc..)
  -d     delete the network agent environment (keys, manifests...).
  -i     display demo information
  -g     display active GCP environment (user, project, GKE cluster,...)
  -y     answer 'yes' to all questions (no ask for confirmation)
  -N     answer 'no' to all questions (no ask for confirmation)

Some typical use cases:
 - To install everything from scratch: ./install.sh -all
 - To install everything from scratch without prompts: ./install.sh -all -y
 - To install everything from scratch, skipping rebuilds: ./install.sh -all -N
 - To create and run a network agent environment including the operator: ./install.sh -c; ./install.sh -s
 - To redeploy the operator alone : ./install.sh -o
 - To (re)deploy the network agent Web UI alone : ./install.sh -n
 - To regenerate the network agent runtime with the same environment setup: ./install.sh -k; ./install.sh -s
 - To recreate a complete environment and runtime from scratch: ./install.sh -k; ./install.sh -d; ./install.sh -c; ./install.sh -s
```

## Installation Workflows

### Simple Installation (Recommended)

For most users, the comprehensive installation is the easiest approach:

```bash
# Set your environment variables first (see Environment Setup section above)
./install.sh -all
```

### Step-by-Step Installation

If you prefer more control over the installation process:

1. **Create the environment configuration:**
   ```bash
   ./install.sh -c
   ```

2. **Start the GCP services (VPCs, GKE Cluster, Network Agent K8s operator, Git repos, etc.):**
   ```bash
   ./install.sh -s
   ```

3. **Build the Free5GC network virtual machine (only needed once):**
   ```bash
   ./install.sh -b
   ```

4. **Deploy all Network Agents and Dashboard:**
   ```bash
   ./install.sh -n all
   ```

### Selective Agent Deployment

You can deploy specific agents individually:

```bash
# Deploy only the dashboard and operations agent
./install.sh -n dashboard,operations

# Deploy only the supervisor agent
./install.sh -n supervisor

# Deploy specific agents (incident and logs agents)
./install.sh -n incident,logs

# Deploy network tools
./install.sh -n networktools

# Deploy all agents
./install.sh -n all
```

### Automated Installation

For CI/CD or automated deployments, use the confirmation flags:

```bash
# Answer 'yes' to all prompts automatically
./install.sh -all -y

# Answer 'no' to all prompts (skip optional steps)
./install.sh -all -N
```

## Environment Management

### Viewing Environment Information

```bash
# Display current GCP environment details
./install.sh -g

# Display demo information and URLs
./install.sh -i
```

### Rebuilding Components

```bash
# Rebuild and redeploy the operator only
./install.sh -o

# Rebuild and redeploy log capture function
./install.sh -l

# Regenerate the runtime with same environment setup
./install.sh -k; ./install.sh -s
```

### Clean Up

```bash
# Stop and delete runtime resources (keeps environment config)
./install.sh -k

# Delete environment configuration (keys, manifests)
./install.sh -d

# Complete cleanup (runtime + environment)
./install.sh -k; ./install.sh -d
```

## Troubleshooting

### Common Issues

1. **Permission Errors**: Ensure your GCP user has Owner role on the project and all organization policies are correctly set.

2. **Environment Variables**: The script will validate all required environment variables and provide clear error messages if any are missing.

3. **Network Connectivity**: If building the VNF image fails with SSH errors, ensure you can connect to GCP from your network (run `gcert` if on Google corporate network).

4. **Resource Quotas**: Ensure your GCP project has sufficient quotas for compute instances, GKE clusters, and other resources.

### Getting Help

- Use `./install.sh -g` to check your current environment configuration
- Use `./install.sh -i` to see all deployed service URLs
- Check the script output for specific error messages and suggested fixes

## What Gets Deployed

The complete installation creates:

- **GKE Cluster**: Kubernetes cluster with Config Connector and operators
- **Network Infrastructure**: VPCs, subnets, firewall rules, and NAT gateways  
- **Database**: Spanner instance for network topology storage
- **Agents**: Multiple specialized network agents (supervisor, engineer, operations, tester, logs, incident)
- **Dashboard**: Web-based network management interface
- **Git Repository**: Gitea server for network configuration management
- **Monitoring**: Log capture and processing functions

All services are deployed to Google Cloud Run for scalability and cost efficiency.
