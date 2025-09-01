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

### 1. Run simulated Users

* Add test agent to the UI, click on 
* Create a test, e.g. "Create a test called test1 from cellsite1-ueransim to DNN dnn"
* Show the service performance UI components

### 2. Create a fault

* Incident builder
* select wireguard appliance and kill the process
* check the fault in the UI

### 3. Resolver Agent progress

