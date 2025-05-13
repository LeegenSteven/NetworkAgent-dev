# Network Agent Architecture

The network agent is a hierarchy of agents consisting of a general agent that can hand over control to specialist agents tuned for a specific task. 

The following agents are included: 

* __Main Agent:__ General agent that routes to specialist agents when necessary.
* __Engineering Agent:__ Specialist for creating and managing network changes.
* __Incident Agent:__ Specialist agent for managing network problems or incidents.

## Main Agent

The main agent responds to general queries from the user about their deployed network services and what action they can take on the network. The __main agent__ routes to a specialist agent when the question requires it. 

![main agent architecture](/drawings/agent/main_agent.drawio.svg)

## Network Engineer Agent

The diagram below describes the specialist network engineer agent workflow. 

![engineer agent architecture](/drawings/agent/engineer.drawio.svg)


## Incident Agent

The specialist incident agent is work in progress. 

