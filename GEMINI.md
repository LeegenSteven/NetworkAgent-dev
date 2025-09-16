# GEMINI.md - Your AI Assistant for the Network Agent Project

This file provides context for me, your Gemini AI assistant, to help you work on the Network Agent project.

## Project Overview

This project is a demonstration of a set of network agents that manage the end-to-end lifecycle of a virtual telecoms network. The agents are built on Google's Autonomous Network Operations Framework and implemented using Google's agent development kit (ADK) and agent-to-agent protocol (A2A).

The system is composed of several key components:

*   **Network Agents:** A collection of specialized agents responsible for various tasks, including:
    *   **Supervisor Agent:** The main entry point for user interaction, routing tasks to other agents.
    *   **Engineering Agent:** Designs and implements network changes.
    *   **Operations Agent:** Queries the current state of the network.
    *   **Test Agent:** Runs tests on the network.
    *   **Logs Agent:** Queries logs.
    *   **Resolver Agent:** Investigates and resolves incidents.
*   **Dashboard:** A Flutter-based web application that provides a user interface for interacting with the agents.
*   **Network Operator:** A Kubernetes operator that manages the lifecycle of network functions and infrastructure.
*   **GCP Services:** The project heavily utilizes Google Cloud Platform services, including:
    *   **Google Kubernetes Engine (GKE):** For orchestrating the network agents and other components.
    *   **Google Spanner:** As a database for network topology, logs, and metrics.
    *   **Google Cloud Run:** For running the network agents and other services.
    *   **Google Cloud Build:** For continuous integration and deployment.

## Building and Running

The `install.sh` script is the primary tool for managing the project's environment. It provides a wide range of options for installing, configuring, and deploying the various components.

### Environment Setup

Before running the `install.sh` script, you must configure your environment by creating a `setenv.sh` file with the required environment variables. A template for this file is provided in `setenv.sh`.

### Key `install.sh` Commands

*   `./install.sh --all`: Performs a comprehensive installation of all components.
*   `./install.sh -c`: Creates the network agent environment (keys, manifests, etc.).
*   `./install.sh -s`: Starts the network agent runtime (GKE cluster, etc.).
*   `./install.sh -b`: Builds the virtual network function image.
*   `./install.sh -n <agent_name>`: Deploys a specific network agent.
*   `./install.sh -k`: Stops and deletes the network agent runtime.
*   `./install.sh -d`: Deletes the network agent environment.

For a full list of options, run `./install.sh -h`.

## Development Conventions

This project follows Google's Open Source Community Guidelines. All contributions must be accompanied by a Contributor License Agreement (CLA). Code reviews are conducted via GitHub pull requests. For more details, see the `CONTRIBUTING.md` file.
