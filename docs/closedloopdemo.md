# Closed Loop Demo

## Overview

This demo showcases how AI agents can cooperate to resolve incidents in the network. 

### Key Agents
- **Resolver Agent**: Investigates faults reported from the network into their root cause and resolution
- **Network Engineering Agent**: Automates the resolution of incidents proposed by the Resolver agent

## Demo Setup

The demo begins with a fully operational 5g network.

### Initial Setup Steps
- Run the [5g build demo](/docs/5gbuilddemo.md) to deploy a fully working 5g network
- Prompt the engineering agent to create a working network, e.g. "Create a working 5gcore with 2 cellsites and radio simulators"

## Demo Script

### 0. Show the operating procedure manual

* The incident repo in gitea has an operating procedure manual the resolver agents uses throughout its incident resolution process. Show the document

### 1. Run simulated Users

* Add test agent through the settings screen 
* Create a test, e.g. "Create a test called test1 from cellsite1-ueransim to DNN dnn"
* Show the service performance UI components

### 2. Create a fault

* In the Incident builder UI, double click on a wireguard appliance connected to a cellsite that has a running test. For example if the test is running on cellsite1-ueransim, then select the cellsite1-vpn-XXX wireguard instance and kill its process
* A fault will be reported in the UI, the fault will have originated from the running test on the ueransim instance because it cannot access the web server in the dnn server. The ueransim instance will be highlighted in the UI and also a red notification appears in the top left of the network dashboard screen.

### 3. Resolver Agent progress

* Click on the notification icon to see the incidents screen, all incidents reported will have a resolver agent investigating their root cause and trying to propose/execute resolutions. 
* The resolver agent progresses through an investigation strategy, troubleshooting and resolution steps. You can see its progress graphically and click on the analysis it has done at each stage. 

## 4. Approve the Network Engineer Plan

* If a resolution is found the resolver agent will ask the network engineer agent to execute the resolution automatically. 
* The network engineer will then ask for permission to proceed from the agent notification screen
* Approve the change

