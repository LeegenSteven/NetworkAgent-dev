# GEMINI.md - Your AI Assistant for the Network Agent Project

This file provides context for me, your Gemini AI assistant, to help you work on the Network Agent project.

## Project Overview

This project demonstrates a set of network agents that manage the end-to-end lifecycle of a virtual telecoms network. The agents are built on **Google's Autonomous Network Operations Framework** and implemented using:

*   **Google's Agent Development Kit (ADK)**
*   **Agent-to-Agent Protocol (A2A)**
*   **LangGraph Agent Framework** (for specialist agents)
*   **Model Context Protocol (MCP)** (for tool integration)

The system uses **Google Kubernetes Engine (GKE)** for orchestration and **Google Spanner** for storing network topology, logs, and metrics.

## System Components

The system is composed of several key components:

### 1. Network Agents

The agents allow users to interact with the network using natural language to manage and monitor services. They can work in chat mode (interactive) or background mode (triggered by events).

*   **Supervisor Agent:** The main entry point for user interaction. It routes user tasks to the appropriate specialist agent and manages the conversation flow.
*   **Operations Agent:** A specialist agent responsible for investigating existing network services and locations. It is read-only and provides information on what is currently deployed.
*   **Engineering Agent:** A specialist agent that designs and implements network changes. It follows a workflow to Build a Plan, Confirm with the User, and Execute Steps to achieve the objective.
*   **Test Agent (User Agent):** A specialist agent for proactively running network tests. It can simulate end-user traffic to verify network functionality.
*   **Resolver Agent:** A background agent responsible for diagnosing and resolving network faults. It orchestrates a sequential workflow of specialist sub-agents:
    *   **Incident Investigator (Strategy):** Gathers initial info and identifies services to investigate.
    *   **TroubleShoot Agent:** Analyzes metrics and data to identify the root cause.
    *   **Resolution Agent:** Executes remediation actions (e.g., network changes, restarts).
*   **Order Agent:** Takes a network slice order and generates requests for the engineering agent to build it. It includes sub-agents for network design, validation, and execution.
*   **Logs Agent:** Responsible for querying and retrieving logs from the system.
*   **Incident Agent:** A background agent that listens for issues and tries to resolve them or trigger the resolver workflow.

### 2. MCP Tools

The project utilizes the **Model Context Protocol (MCP)** to expose capabilities to the agents. These tools allow agents to interact with the underlying infrastructure (GKE, Spanner) and perform actions like:
*   Listing/Getting network services and locations.
*   Fetching metrics and logs.
*   Applying network changes (via the Network Operator).

### 3. Other Components

*   **Dashboard:** A Flutter-based web application that provides a chat interface for interacting with the agents and visualizes network status.
*   **Network Operator:** A Kubernetes operator that manages the lifecycle of network functions and infrastructure.
*   **Fault Service:** Catches fault logs (via GCP Log Sink & Pub/Sub) and triggers the Resolver Agent.

## Building and Running

The `install.sh` script is the primary tool for managing the project's environment.

### Environment Setup

1.  Create a `setenv.sh` file with required environment variables (use `setenv.sh` template).
2.  Source the file or ensure variables are available.

### Key `install.sh` Commands

*   `./install.sh --all`: Comprehensive installation of all components.
*   `./install.sh -c`: Creates the network agent environment (keys, manifests, etc.).
*   `./install.sh -s`: Starts the network agent runtime (GKE cluster, etc.).
*   `./install.sh -n [agent_name]`: Deploys network agents.
    *   Valid names: `all`, `supervisor`, `engineer`, `dashboard`, `operations`, `test`, `resolver`, etc.
*   `./install.sh -k`: Stops and deletes the network agent runtime.
*   `./install.sh -d`: Deletes the network agent environment.

Use `./install.sh -h` for a full list of options.

## Development Conventions

*   **Documentation:** Refer to the `docs/` directory for detailed architecture and design documents (e.g., `docs/agent.md`).
*   **Contribution:** Follow Google's Open Source Community Guidelines. See `CONTRIBUTING.md`.
