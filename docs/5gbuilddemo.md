# 5G Service Demo

## Demo setup

Setup your environment as discussed in the [environment setup guide](/environment/Readme.md). 

To install the demo environment run the following commands (this takes a while - about an hour). 

```
cd NetworkAgent
./install -c
./install -s
./install -n
```

If you have not built the free5gc network functions, then there is an additional step to do so, as follows:

```
cd NetworkAgent
kubectl apply -f environment/free5gc-build.yaml
```

This also takes a long time, but is a one time build. A virtual machine is created that builds the various containers and pushes to __networkagent__ artifact registry. When you see a ueransim image appear in the registry the build is complete. 

You can delete the virtual machine by running the following

```
kubectl delete -f environment/free5gc-build.yaml
```

The demo starts with a partially setup 5G network, i.e. the Core network components are installed but nothing else. You can install the core components from git. When the install script finishes above it will provide a link to the git server. 

* Open the git server and login with the username and password you set in the environment setup guide above. 
* Select the __core__ repo
* Select the __networkfunction__ branch and you will see the k8s CRs that need to be instantiated
* Create a new pull request that merges the __networkfunction__ branch into master
* Once complete this will trigger the __core__ network functions to be instantiated through GKE to your project. This will take about 15 mins to complete, you can see automation logs as they happen in the demo dashboard.


## Demo Introduction

* In this demo we will talk about how AI agents can accelerate a telco’s journey to a more autonomous network 
* In our demo we have a number of agents we can chat with or can carry out tasks in the background triggered by external events.
* We have a network engineering agent that simplifies network design and build, better automating that process and reducing errors along the way. We have an operations agent who can tell us about our existing network and a test agent that can run tests
* We also have a network optimisation agent that runs in the background and helps to auto resolve incidents and improve overall network performance, often by collaborating with the network engineering agent to make changes

## UI Tour
 
* __[Show UI with partial deployed nework]__
* This is the network agent User Interface we built for this demo. The code for this UI was 100% written by an AI agent. We didn't directly write a single line of this code by hand. And from scratch it took a couple of days to produce what you see here.
* Lets take a quick tour. Our agents manage an end to end virtual 5G network running on google cloud. You can see the topology of what is currently deployed here.
* Right now we have a partially deployed 5g network, you can see we have a control plane, user plane and a fake internet all connected to a number of google cloud virtual networks.
* As our agents make network changes you will see those changes appear here.
* __[Click on a node]__
  * If we click on something we can see the current configuration and status of a network function. The agents have access to google cloud automation and monitoring tools that manage the network so can access all this information
* __[Open Logs]__
  * The agents also have access to all logs from the automation tools and the network functions themselves. The incident agent can for example can use this to diagnose the root cause of issues
* __[Click on Metrics]__
  * And finally the agents also have access to all network performance metrics that are being pushed to google cloud in real time.  The incident agent can run tests and use this data to correlate performance.
* __[Click on Chat]__
  * We can chat with out agents here. Lets see if any agents exist already
  * "what can you do for me?"
  * "what agents are there?"
  * You can see there is nothing right now. 

## Show gitops & tools(Extended Version)

To show gitops approach to making network intent changes, open gitea and login.

* Select the core repo and show the CRs that are responsible for whats on the UI
* Select the cellsite2 repo and create a pull request to merge the location branch into master. This will cause a new VPC to be created called cellsite2 and will show on the UI screen. You should also be able to see logs in action as this happens

## Show the tools

Run the MCP inspector tool, as follows:

```
npx @modelcontextprotocol/inspector
```

Enter the cloud run __networktools__ url in MCP inspector and connect. Show the available tools the network agents have access to. 


## Add Operations Agents

* Lets add an operations agent that can help us find out about our network services and see whats going on with our network services.
* __[Add Cloudrun address for operationsagent]__
  * "What network services can i deploy?"
  * "More details on UERanSIM"
  * "Any rules i should know about"
* The agent has learned this information from our automation platform and from technical network design documentation that describes how to lay out and configure our network. There is no programmed logic in the agent itself, so if we update our automation capabilities or update our network design documents, the agent can reason based on that new information dynamically. 
* Lets see what services are already deployed
  * "What network services are already deployed"
  * "What is the status of the control plane network service?"
  * "What locations are there?"

## Add the Network Engineer agent

* Let me clear the chat and lets start making some changes <press chat clear>
* Lets add a network engineering agent that can help us make changes to the network
* __[Add Cloudrun address for engineeragent]__
* We need to add more network functions to have a complete running end to end 5G service
* I’ve noticed there is no cellsite so lets ask the agent to start creating one 
  * "Create a plan to deploy a new network location called cellsite1 with cidr 10.0.40.0/24"
* Now lets ask the agent to figure out something more complex, I’m deliberately going to be a bit vague so it has to do some reasoning
  * Can you create a plan to add a radio simulator to cellsite1 and a working 5G network
  * You can see in the plan, it includes a point to point vpn service, because our network design documentation says we need a VPN to connect network locations for the 5G network functions to reach each other. 
* I can ask it to  change any of these details, e.g. 
  * Can you change the the name of the ptp service to brian-ptp
  * Can you reorder the tasks so the ptp service is before/after the ueransim task
  * “Yes” to approve the execution
* __Open the logs panel__
  * As this comes to life you can see the logs from network functions again all stored in Google Cloud so they can be searched by the agents later
* __Open the metrics screen__
  * Also we can see more performance metrics for all the network functions we have deployed. We are collecting metrics from the network in real and showing here. This is all available to the agent for analysis
  * When this finishes we will have an operational 5g mobile network and other agents can run tests to test it works

## Run tests with the Test Agent

* __[Add Cloudrun address for testagent]__
* Ask the model to run a test
* Show metrics live

## What did we see: 

* Dynamically adding A2A agents
* Our network engineering agent understands how to plan network changes dynamically from network design documentation and available network automation descriptors
* Also don’t forget the code for the UI we were using was 100% generated by Gemini

