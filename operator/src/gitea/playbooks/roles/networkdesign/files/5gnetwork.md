# 5G Network Design

A fully operational 5G network service will need one control plane, one user plane, one data network and one or more radio simulators 
to function correctly. 

5G and connectivity network services that can be orchestrated are represented as a set kubernetes custom resources (CRD's). The lifecycle of Network Services is managed by creating and deleting custom resources described by the network service CRDs.

The network service CRDs below provide the following information:
- description of the network service functionality
- a spec section that has the name of the 'kind' for each network service and an OpenAPI schema describing the information required to 
  instantiate the network service kind.
- dependencies on other network service instances or network locations for this network service to work correctly
- configuration rules that must be true across all network services for them to work properly. 

If you need to propose names and namespace for new network services or locations use the following guidelines:
- new network location names and namespaces are at your discretion to propose
- When creating new network locations the following CIDR ranges are not to be used, i.e. these CIDRs are already used by the system
  - 10.0.0.0/24
  - 10.0.100.0/24
  - 10.60.0.0/24
- When creating new network locations check that the ip address with cidr for existing network locations
- new network service names are at your discretion, but namespaces must be the same as the network locations they are configured with
- new connectivity service names are at your discretion but always have the namespace 'vpn'
- DataNetwork, ControlPlane and UserPlaneFunction network services are always deployed in the same namespace.
- UserPlaneFunction and UERanSim network services must not be assigned the same network locations. 
- The network location assigned to DataNetwork network service should be the same namespace as the network location assigned to the UPF network location
- the dataplane network location is a reserved network location, you must not use it in your planned steps.

Network locations and network services can be specified as "name"/"namespace", e.g. core/core, or cellsite1-radio1/cellsite1. The name is always first and namespace 
follows the /

Network locations attached to UERanSim and UserPlaneFunction network services must be attached to a connectivity service so traffic can be carried between them. 
When connecting more than two network locations you should use a Mesh connectivity service with multiple interfaces.
