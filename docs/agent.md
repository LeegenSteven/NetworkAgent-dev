# Network Agent Architecture

The networkagent demo includes a set of agents that a user can interact with using natural language to manage and monitor network services. Investigative agents can work in the background triggered by external network events and request network changes to resolve issues or optimise the performance of the network. 

The figure below shows the high level network agent architecture. 

![agent architecture](/drawings/agent/agent_architecture.drawio.svg)

Network automation and observability capabilities are provided with GKE and Spanner. These capabilities are exposed to the Agents through a set of MCP Tools. 

The User can interact with the agents through a Dashboard User Interface and Agents can communicate with each other using the A2A prototol. 

## Chatting with Agents

A supervisor agent takes user input, sends tasks to an agent of its choice and updates the chat when task status changes are received from other agents. The supervisor will hand over control to a specialist agent until that task is complete (unless the conversation is reset by the user). 

The following agents are available for chat interaction:

* __Supervisor Agent:__ General agent that routes to specialist agents.
* __Operations Agent:__ Specialist for investigating existing network services and locations.
* __Engineering Agent:__ Specialist for creating and managing network changes.
* __Test Agent:__ Specialist agent for proactively running network tests.

The figure below shows a high level software architecture and chat interaction patterns between the agents. A set of tools allowing the agents to interact with GKE and Spanner is available to all agents.  

![agent hierarchy](/drawings/agent/chatagents.drawio.svg)

The supervisor agent is built using the [ADK framework](https://google.github.io/adk-docs/) and communicates with the specialist agents with the [A2A framework](https://google.github.io/A2A/).

Specialist agents are implemented with [langgraph agent framework](https://langchain-ai.github.io/langgraph/) and integrate with the agent tools using [MCP](https://modelcontextprotocol.io/introduction).

### Chat Interaction

The interaction diagram below shows the flow of interactions to support a chat session with a user. This diagram assumes remote agents have already been added to the system and are ready to be routed to by the supervisor agent.

![chat interaction](/drawings/agent/chatinteraction.drawio.svg)

The flow is summarised as follows

* The dashboard app communicates with the supervisor agent through a socket interface. When the dashboard connects, its socket id is stored. 
* The user types a request through the Chat interfaxe and a socket event is sent to the supervisor agent. The supervisor agent creates a new agent session id if it doesnt already exist. This session id can be reset by the user through the chat interface at any time to reset the agent's session memory.  
* The Supervisor agent routes the users request one of the remote agents it is aware of, or it tells the user it is not able to service their request. 
* The supervisor creates an A2A SendStreamingMessageRequest with the content of the users request, the context_id uses the agent session id created earlier. This message is then sent to the remote A2A agent
* The remote agents sends back a SendMessageSuccessResponse which includes a Task with its task_id. This task_id is added to the supervisor agent state for use later.
* The remote agent can also send a series of SendMessageSuccessResponse's with TaskUpdateStatus events reporting the current task state, task update events are handled as follows depending on their TaskState:
  * TaskState=__input_required__: Agent is requesting information from the user. The supervisor summarises the agent request and updates the supervisor ADK state to track we are waiting for user input for this task id. In response to this request for information another SendStreamingMessageRequest is sent to the remote agent, this time including the task_id and context_id along with the users response. 
  * TaskState=__working__: The agent is running through its flow successfully and can send status update events to show progress. The supervisor passes these events straight to the dashboard chat interface through the socket.
  * TaskState=__completed__: The agent has finished its task and provides a final response, which is summarised by the supervisor and a final response sent back to the dashboard through the socket. 


## Background Agents

Agents can also be triggered by external events or from MCP tool server, e.g. newly reported anomalies in spanner can trigger an anomaly agent to investigate. 

![background](/drawings/agent/background.drawio.svg)

The figure above shows the background agent interaction pattern.

* The Supervisor agent exposes a PushNotification endpoint that can be used as a callback by the remote agents. 
* The Supervisor agent sends these events to the dashboard UI over the socket interface so they can be viewed by the user. In this way any remote agent background tasks that need user input can be alerted to the user. 
* External events can trigger a new background Agent task that in turn can request another remote agent to perform an additional task after it has completed.
* Remote Agents publish status update events to the supervisor Push Notification endpoint when they need addition input to proceed.

The interaction chart below shows the sequence of events. 

![agent interaction](/drawings/agent/background-sequence.drawio.svg)

Calls from one agent to another are expected to be non-streaming and include a data payload, chat interactions are expected to be streaming with text payload. 

When agents create a task to another agent, the client agent is expected to poll for the task status to complete by sending GetTask requests and looking for completing status, e.g. __completed__, __failed__, __cancelled__. 

## Agents

The following sections present a desciption of each agent.

### Supervisor Agent

The main supervisor agent responds to general queries from the user about their deployed network services and what action they can take on the network. The __main agent__ routes to a specialist agent when the question requires it. 

![main agent architecture](/drawings/agent/main_agent.drawio.svg)

The supervisor agent has access to two tools

1. Tool to list all remote A2A agents it knows about and their A2A Card details, listing their capabilities etc. 
2. Tool to send a task to one of the remote agents

The supervisor agent maps the users question to a tasks it asks of the remote agents to complete. These tasks can be interactive with that remote agent until their are complete. 

If the supervisor agent cannot map to an agent it can clarify the context with the user until it finds an agent to help or do nothing.

### Operations Agent

Operations agent provides the user with information on what network services can be deployed and what network services are already deployed. This agent is informational only has and has access only to the read only tools from the MCP server.

### Network Engineer Agent

The network engineer agent performanes changes to network services and locations through the non read only tools from the MCP server. The network engineer agent comes up with a plan of changes and confirms they are correct with the user. 

The diagram below describes the network engineer agent workflow. 

![engineer agent architecture](/drawings/agent/engineer.drawio.svg)

The engineer steps are as follows:

* __Build Plan:__ The flow is kicked off with a user __objective__ to create something in the network. The node retrieves available service descriptors (GKE CRD automation), whats been deployed already, and existing network locations from the MCP tools. This is then fed into a prompt that generates a set of steps that can achive the user objective.
* __Confirm Plan__: The plan is sent to the user and iterated until the user confirm he/she is ok with it. 
* __Execute Step__: Each step is executed, with Gemini mapping the step to a tool call in the MCP server. 
* __Run Tool__: The tool and its arguments are sent from the previous step and passed to the MCP server.
* __Summarise Response__: Gemini summarises the execution of the plan and passes back to the supervisor node and marks the task as complete. 

### User Agent

The user agent can simulate end user traffic on the network. This agent only has access only to the simulation related tools from the MCP server.


### Incident Resolver Agent

Network faults are reported from a number of sources:

1. the scripts that simulate user traffic tests if they encounter errors trigger a fault. 
2. liveness probes watching network function software can generate faults if the processes they are watching die. 

The fault logs are caught by a GCP Log Sink and sent to a pub/sub topic which in turn triggers an event into a Cloud Run Fault Service. The Cloud Run Fault Service correlates faults with existing incidents being investigated and if there is no current incident a new one is created and the resolver agent is asked to investigate and try to resolve. 

![fault trigger](/drawings/agent/faulttrigger.drawio.svg)

The Incident Resolver Agent is implemented using [Google's ADK ](https://google.github.io/adk-docs/) framework. The resolver agent operates as a background agent that receives incident data through A2A messaging and coordinates a sequential workflow of specialist agents to investigate and resolve network incidents automatically.

![fault agent](/drawings/agent/resolver_agent.drawio.svg)

#### Resolver Agent Architecture

The resolver agent is structured as a `SequentialAgent` that orchestrates three specialist agents in a defined order:

1. **Incident Investigator Agent** (Strategy Agent)
2. **TroubleShoot Agent** 
3. **Resolution Agent**

When triggered by an incident, the resolver agent:

* Receives incident data via A2A messaging protocol
* Retrieves the incident operating procedure documentation from the tools MCP server
* Creates a session with the incident data and operating procedures as context
* Executes the sequential workflow of specialist agents
* Reports progress and completion status back to the supervisor agent

#### Strategy Agent (Incident Investigator)

The Strategy Agent is implemented as a `SequentialAgent` containing two sub-agents:

* **Analyse Incident Agent**: Gathers initial information about affected network services, locations and resources using MCP tools (`get_node_details`, `get_connected_nodes`, `get_node_path`, `get_nodes_networking_by_kind`)
* **Incident Details Agent**: Collects additional detailed information about the incident

The output expected from this agent is comprehensive incident information and identification of network services to investigate further as potential root causes.

#### TroubleShoot Agent

The TroubleShoot Agent is implemented as an `LlmAgent` that investigates the network services identified by the Strategy Agent. It uses MCP tools to:

* Get parent network service information (`get_parent_network_service`)
* Fetch metrics data (`fetch_last_metrics_by_name`)

The agent analyzes this data to identify the specific root cause, including the compute instance, software, or misconfiguration issue that caused the incident. The output is stored with the key `root_cause`.

#### Resolution Agent

The Resolution Agent is implemented as an `LlmAgent` that identifies and executes solutions for the reported root cause. Key capabilities include:

* **Network Change Execution**: Uses the `make_network_change` tool to communicate with the Engineer Agent via A2A protocol
* **Engineer Agent Integration**: Sends network change requests to the Engineer Agent, which can create plans for network locations, services, and reinstall failed components
* **Automated Remediation**: Executes configuration changes, service restarts, or resource adjustments based on the identified root cause

The Resolution Agent can request changes such as:
- Creating or deleting network locations
- Creating, deleting, or reinstalling network services  
- Reinstalling failed network components (e.g., "reinstall a failed wireguard network service named cellsite1-vpn1234")

The output is stored with the key `resolution` and includes the executed remediation actions.

#### Workflow Integration

Each specialist agent includes callback functions for:
* **Database Updates**: Recording progress and results in the system database
* **Supervisor Notification**: Sending status updates to the supervisor agent for user visibility

The resolver agent operates autonomously but provides transparency through status updates and can integrate with the broader agent ecosystem for complex remediation scenarios.
