__Transport Network__

* What about firewalls?
* Static transport network setup via operator
* Ability to provision L3VPN on physical topology via operator
* Run realistic traffic via operator
* GCP Ops Agent collect all metrics and syslog from vyos ++

__Spanner__

* Update Spanner schema to support GNN training
    * Capture device config changes
    * Capture device interface performance metrics 
    * Add flows and how they map to interfaces?
* Update operator to capture vyos/l3vpn and update spanner model

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

* Update engineering agent with transport
* Add what if to validate engineering agent output
