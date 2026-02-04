__Network__

* Split VyosNetwork CRDs (Brian)
    * Static transport network setup
    * CRD for routing
    * CRD for L3VPN
* GCP Ops Agent (Laurent)
    * syslog from vyos
    * vyos metrics
    * Logsink -> spanner
* Traffic Simulator
    * X number of users with predictable time of day pattern
    * Update the traffic UI viewer
* Add free5gc to NetworkVM (Brian)
    * generate an image for everything

__Spanner__

* Temporal Spanner schema 
    * transport topology
        * physical connectivity device->interface->connection
        * logical service service->interface
    * Capture device config & netbert embedding
    * Capture device interface performance metrics 
    * Service Performance metrics
* Update graph operator 
    * operator to capture vyos/l3vpn and update spanner model
    * trigger a snapshot to be processed by GNN
* Network Dashboard
    * Update topology viewer with a map? add geo in descriptor

__GNN(s)__

* GNN models
    * Anomaly detection and RCA
    * What if prediction 
    * Feature engineering
        * Router - config
        * Interface - metrics
        * Flow - metrics
* Training pipeline
    * One off model training kicked off from UI
    * Store model weights somewhere
* Inference engine/cloud run
    * Periodic spanner snapshot query
    * Run snapshot through trained GNN
    * Anomaly notification
        * Device cluster affected
        * Root cause as per GNN

__Agents__

* Update engineering agent 
    * New Vyos transport CRDs
* What if agent
    * to validate engineering agent output
