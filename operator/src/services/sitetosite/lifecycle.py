import logging
from services.sitetosite.lifecycle_tasks import *
import json
import os

logger = logging.getLogger(__name__)

##########################################
# Create a new PTP VPN
##########################################
@kopf.on.create('pointtopointservice')
async def ptpservice(spec, name, namespace, logger, **kwargs):
  logger.info(f"Create pointtopoint service handler is called with spec: {spec}")

  if len(spec.get('interfaces'))!=2:
      raise kopf.PermanentError("Two interfaces must be provided.")

  # build the a and b end variables
  aend=spec.get('interfaces')[0]
  bend=spec.get('interfaces')[1]

  avars = create_aend_vars(aend, bend)
  logger.info(json.dumps(avars, indent=4))
  bvars = create_bend_vars(aend, bend)
  logger.info(json.dumps(bvars, indent=4))

  # Create VPCs for a & b
  await create_network(avars['vmname'])
  await create_network(bvars['vmname'])

  # Create subnets and NAT for a & b VPCs
  await create_subnetwork(avars['vmname'], avars['vmname'], avars['cidr'], os.getenv("REGION"))
  await create_subnetwork(bvars['vmname'], bvars['vmname'], avars['cidr'], os.getenv("REGION"))

  # Create compute routers for a & b VPCs
  await create_router(avars['vmname'], os.getenv("REGION"))
  await create_router(bvars['vmname'], os.getenv("REGION"))

  # Create NAT to allow outbound for a & b VPCs
  await create_nat(avars['vmname'], os.getenv("REGION"))
  await create_nat(bvars['vmname'], os.getenv("REGION"))

  # Open ports for a & b VPCs
  await create_wg_rule(avars['vmname'])
  await create_wg_rule(bvars['vmname'])
  await create_ssh_rule(avars['vmname'])
  await create_ssh_rule(bvars['vmname'])

  # Create VMs for a & b ends
  await create_compute(avars['vmname'], avars['vmname'], aend, os.getenv("PROJECT"),os.getenv("REGION"),os.getenv("ZONE"), avars['mgmtsubnetname'])
  await create_compute(bvars['vmname'], bvars['vmname'], bend, os.getenv("PROJECT"),os.getenv("REGION"),os.getenv("ZONE"), bvars['mgmtsubnetname'])

  # Create external ips for bot compute ends
  await create_external_ip(avars['vmname'], os.getenv("REGION"))
  await create_external_ip(bvars['vmname'], os.getenv("REGION"))

  # Provision the VPNs in each compute
  await create_vpn_edge(avars['vmname'], avars['vmname'], avars['tunnelsubnet'], avars['tunneladdress'], avars['peerinterface'], avars['peername'])
  await create_vpn_edge(bvars['vmname'], bvars['vmname'], bvars['tunnelsubnet'], bvars['tunneladdress'], bvars['peerinterface'], bvars['peername'])

  return {
    'a':{'networkname': avars['vmname'], 
         'subnetworkname': avars['vmname'], 
         'routername': f"{avars['vmname']}-router",
         'routernatname': f"{avars['vmname']}-nat",
         'computename': avars['vmname']
        },
    'b':{'networkname': bvars['vmname'],
         'subnetworkname': bvars['vmname'], 
         'routername': f"{bvars['vmname']}-router",
         'routernatname': f"{bvars['vmname']}-nat",
         'computename': avars['vmname']
        }
  }

###########################################
# Simple variable creation for a and b ends
###########################################
def create_aend_vars(aend, bend):
  extravars = {
      'vmname': aend+'-vpn',
      'cidr': '10.10.10.0/24', 
      'mgmtsubnetname': 'mgmt-subnet',
      'interface': aend,
      'peerinterface': bend,
      'tunnelsubnet': '192.168.1.0/24',
      'tunneladdress': '192.168.1.1',
      'peername': bend+'-vpn'
  }
  return extravars

def create_bend_vars(aend, bend):
  extravars = {
      'vmname': bend+'-vpn',
      'cidr': '10.10.11.0/24', 
      'mgmtsubnetname': 'mgmt-subnet',
      'interface': bend,
      'peerinterface': aend,
      'tunnelsubnet': '192.168.1.0/24',
      'tunneladdress': '192.168.1.2',
      'peername': aend+'-vpn'
  }
  return extravars
