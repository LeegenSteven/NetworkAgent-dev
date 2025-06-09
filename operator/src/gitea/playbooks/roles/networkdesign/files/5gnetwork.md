# 5G Network Design

A fully operational 5G network service will need one control plane, one user plane, one data network and one or more radio simulators to function correctly. 

If you need to propose names for new network services or locations use the following guidelines:
- new network location names are at your discretion to propose
- When creating new network locations the following CIDR ranges are not to be used, i.e. these CIDRs are already used by the system
  - 10.0.0.0/24
  - 10.0.100.0/24
  - 10.60.0.0/24
- When creating new network locations check that the ip address with cidr for existing network locations
- new network service names are at your discretion
- new connectivity service names are at your discretion
- UserPlaneFunction and UERanSim network services must not be assigned the same network locations.
- The network location assigned to DataNetwork network service should be the same as the network location assigned to the UPF network location
- the dataplane network location is a reserved network location, you must not use it in your planned steps.

Network locations attached to UERanSim and UserPlaneFunction network services must be attached to a connectivity service so traffic can be carried between them. 

When connecting more than two network locations you should use a Mesh connectivity service with multiple interfaces.
