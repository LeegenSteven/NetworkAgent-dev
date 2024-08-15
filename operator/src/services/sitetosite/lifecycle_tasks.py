import logging
import kubernetes
import kopf
import json
import os
from utils.compute import *

logger = logging.getLogger(__name__)

########################################################################
# Create wg ComputeFirewall rule
########################################################################
async def create_wg_rule(network_name):
  logger.info("Create wireguard firewall rule")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeFirewall",
  )

  crd_manifest= {
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeFirewall",
    "metadata": {
      "name": f"{network_name}-wg",
      "namespace": "automation"
    },
    "spec": {
      "allow": [
        {
          "protocol": "udp",
          "ports": [
              "51820"
          ]
        }
      ],
      "networkRef": {
        "name": network_name
      },
      "direction": "INGRESS",
      "sourceRanges": [
        "0.0.0.0/0"
      ]
    }
  }
  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)
  logger.debug(json.dumps(crd_manifest, indent=4))

  try:
    result = network_api.create(crd_manifest)
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    if e.status == 409:
      logger.info("Already exists - skipping")
    else:
      logger.debug(e)

########################################################################
# Create ssh ComputeFirewall rule
########################################################################
async def create_ssh_rule(network_name):
  logger.info("Create ssh firewall rule")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeFirewall",
  )

  crd_manifest= {
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeFirewall",
    "metadata": {
      "name": f"{network_name}-ssh",
      "namespace": "automation"
    },
    "spec": {
      "allow": [
          {
              "protocol": "tcp",
              "ports": [
                  "22"
              ]
          }
      ],
      "networkRef": {
        "name": network_name
      },
      "direction": "INGRESS",
      "sourceRanges": [
        "0.0.0.0/0"
      ]
    }
  }

  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)
  logger.debug(json.dumps(crd_manifest, indent=4))

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    if e.status == 409:
      logger.info("Already exists - skipping")
    else:
      logger.debug(e)


########################################################################
# WireguardAppliance
########################################################################
async def create_vpn_edge(vpn_name, vm_name, tunnel_subnet, tunnel_ip, peer_interface, peer_vm_name, my_keys, peer_keys):
  logger.info("Create VPN Edge")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="google.dev/v1", 
      kind="WireguardAppliance",
  )

  crd_manifest = {
    "apiVersion": "google.dev/v1",
    "kind": "WireguardAppliance",
    "metadata": {
      "name": vpn_name,
      "namespace": "automation"
    },
    "spec": {
      "vmname": vm_name,
      "tunnelSubnet": tunnel_subnet,
      "tunnelAddress": tunnel_ip,
      "allowedInterface": peer_interface,
      "peer": peer_vm_name,
      "keys": my_keys,
      "peerKeys": peer_keys
    }
  }

  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)
  # logger.debug(json.dumps(crd_manifest, indent=4))

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    # logger.debug(e)
    if e.status == 409:
      logger.info("WG already exists - skipping")

########################################################################
# Create Site
########################################################################
async def create_site(aend, bend, cidr, tunnel_address, uuid, akeys, bkeys):
  logger.info("Creating VPN Site")
  vars = {
      'vmname': aend+'-vpn-'+uuid,
      'cidr': cidr, 
      'mgmtsubnetname': 'mgmt-subnet',
      'interface': aend,
      'peerinterface': bend,
      'tunnelsubnet': '192.168.1.0/24',
      'tunneladdress': tunnel_address,
      'peername': bend+'-vpn-'+uuid,
  }

  logger.info(json.dumps(vars, indent=4))

  # create children resources
  await create_network(vars['vmname'])
  await create_subnetwork(vars['vmname'], vars['vmname'], vars['cidr'], os.getenv("GOOGLE_REGION"))
  await create_router(vars['vmname'], os.getenv("GOOGLE_REGION"))
  await create_nat(vars['vmname'], os.getenv("GOOGLE_REGION"))
  await create_wg_rule(vars['vmname'])
  await create_ssh_rule(vars['vmname'])
  await create_compute(vars['vmname'], vars['vmname'], aend, os.getenv("GOOGLE_PROJECT"),os.getenv("GOOGLE_REGION"),os.getenv("GOOGLE_ZONE"), vars['mgmtsubnetname'])
  await create_external_ip(vars['vmname'], os.getenv("GOOGLE_REGION"))
  await create_vpn_edge(vars['vmname'], vars['vmname'], vars['tunnelsubnet'], vars['tunneladdress'], vars['peerinterface'], vars['peername'], akeys, bkeys)

  # Create the static routes to vpn tunnels
  await create_route(vars['vmname'], aend, bend)

  return vars