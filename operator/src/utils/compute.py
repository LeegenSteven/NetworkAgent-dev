# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import kubernetes
import kopf
import json
import os
import utils.constants as constants

logger = logging.getLogger(__name__)

def get_resource_api(api_version, kind, client=None):
  client = client or kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  resource_api = client.resources.get(api_version=api_version, kind=kind)
  return resource_api

########################################################################
# Create ComputeNetwork
########################################################################
async def create_network(namespace, network_name):
  logger.debug("Create compute network %s", network_name)
  network_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeNetwork")
  crd_manifest={
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeNetwork",
    "metadata": {
      "name": network_name,
      "namespace": namespace,
      "labels": {
        "graph": "true"
      },
      "annotations": {
        "configmanagement.gke.io/managed": "disabled"
      }
    },
    "spec": {
      "routingMode": "REGIONAL",
      "autoCreateSubnetworks": False
    }
  }

  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)
  logger.debug(json.dumps(crd_manifest, indent=4))

  try:
    result = network_api.create(crd_manifest)
    return result
  except kubernetes.client.rest.ApiException as e: 
    if e.status == 409:
      logger.info("Compute network %s already exists - skipping", network_name)
    else:
      logger.debug(e)

########################################################################
# Delete ComputeNetwork
########################################################################
async def delete_network(namespace, network_name):
  logger.debug("Delete compute network %s", network_name)
  network_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeNetwork")
  try: 
    result = network_api.delete(name=network_name, body={}, namespace=namespace)
  except kubernetes.client.rest.ApiException as e: 
    logger.error("Exception raised while deleting network %s: %s", network_name, e.status)
    logger.debug(e)

########################################################################
# Create ComputeSubNetwork
########################################################################
async def create_subnetwork(namespace, network_name, subnet_name, cidr, region):
  logger.debug("Create compute subnetwork")
  network_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeSubnetwork")
  crd_manifest= { 
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeSubnetwork",
    "metadata": {
      "name": subnet_name,
      "namespace": namespace,
      "annotations": {
        "configmanagement.gke.io/managed": "disabled"
      },
      "labels": {
        "graph": "true"
      }
    },
    "spec": {
      "ipCidrRange": cidr,
      "region": region,
      "description": f"{subnet_name} VPN Sub Network",
      "networkRef":{
        "name": network_name
      }
    }
  }

  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)
  logger.debug(json.dumps(crd_manifest, indent=4))

  try:
    result = network_api.create(crd_manifest)
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 409:
      logger.info("Subnetwork %s already exists - skipping", network_name)
    else:
      logger.debug(e)

########################################################################
# Get Subnetwork
########################################################################
async def get_subnetwork(namespace, name):
  logger.debug("Get compute subnetwork %s in namespace %s", name, namespace)
  network_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeSubnetwork")
  try:
    result = network_api.get(namespace=namespace, name=name)
    return result
  except kubernetes.client.rest.ApiException as e:
    if e.status == 404:
      logger.warning("%s in namespace %s not found", name, namespace)
    else:
      logger.error("Exception raised while getting subnetwork %s: %s", name, e.status)
      logger.debug(e)

########################################################################
# Get Network
########################################################################
async def get_network(namespace, name):
  logger.debug("Get compute network %s", name)
  network_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeNetwork")
  try:
    result = network_api.get(namespace=namespace, name=name)
    return result
  except kubernetes.client.rest.ApiException as e:
    logger.error("Exception raised while deleting network %s: %s", name, e.status)
    logger.debug(e)

########################################################################
# Create ComputeRouter
########################################################################
async def create_router(namespace, network_name, region):
  logger.debug("Create Router")
  network_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeRouter")
  route_name = f"{network_name}-router"
  crd_manifest={
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeRouter",
    "metadata":{
      "name": route_name,
      "namespace": namespace,
      "labels": {
        "graph": "true"
      },
      "annotations": {
        "configmanagement.gke.io/managed": "disabled"
      }
    },
    "spec": {
      "description": f"{network_name} vpn router",
      "region": region,
      "networkRef": {
        "name": network_name 
      }
    }
  }

  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)
  logger.debug(json.dumps(crd_manifest, indent=4))

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 409:
      logger.info("Route %s already exists - skipping", route_name)
    else:
      logger.debug(e)

########################################################################
# ComputeRouterNAT
########################################################################
async def create_nat(namespace, network_name, region):
  logger.debug("Create NAT")
  network_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeRouterNAT")
  nat_name = f"{network_name}-nat"
  crd_manifest={
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeRouterNAT",
    "metadata": {
      "name": nat_name,
      "namespace": namespace,
      "labels": {
        "graph": "true"
      },
      "annotations": {
        "configmanagement.gke.io/managed": "disabled"
      }
    },
    "spec": {
      "region": region,
      "routerRef": {
        "name": f"{network_name}-router",
      },     
      "natIpAllocateOption": "AUTO_ONLY",
      "sourceSubnetworkIpRangesToNat": "ALL_SUBNETWORKS_ALL_IP_RANGES"
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
      logger.info("NAT %s already exists - skipping", nat_name)
    else:
      logger.debug(e)

########################################################################
# Create ComputeInstance
########################################################################
async def create_compute(namespace, parent_name, vm_name, external_ip, interfaces, project, region, zone, vpn=False, monitor=True,release="ubuntu-2204-lts",graph=True):
  logger.debug("Create compute %s", vm_name)
  compute_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeInstance")

  # get the google user
  google_user = os.getenv("GOOGLE_USER")
  if google_user is None:
    raise kopf.PermanentError("No GOOGLE_USER environment variable.")

  google_ssh_pub=None
  with open(f'{constants.basedir}/google-compute.pub') as f:
    google_ssh_pub=f.read()
  if google_ssh_pub is None:
    raise kopf.PermanentError("No public ssh key found")

  # create the network interfaces for this VM
  networkInterfaces=[]

  # always add the mgmt interface
  networkInterfaces.append(
    {
      "subnetworkRef": {
        "external": f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}/subnetworks/mgmt-subnet"
      }
    }
  )

  # provision external ip if it is specified
  if external_ip is not None:
    accessconfig=[]
    accessconfig.append({
      "natIpRef": {
          "external": external_ip
      }
    })
    networkInterfaces[0]['accessConfig']=accessconfig

  # if this is a vpn VM then connect to the dataplane
  if vpn:
    networkInterfaces.append(
        {
          "subnetworkRef": {
            "name": "dataplane",
            "namespace": "automation"
          }
        }
    )

  # for VNFs we need to build the metadatastartupscript for network routes
  routes = ['10.0.40', '10.0.50', '10.0.60']
  route_script=""
  router_address=None

  # next add the interface to connect to - this equates to ens5 internal nic
  if interfaces is not None:
    for interface in interfaces:
      # check if the interface has already been added, if not then continue
      for ni in networkInterfaces:
        if 'name' in ni['subnetworkRef'] and ni['subnetworkRef']['name']== interface['name']:
          continue

      # check the interface is up, and wait if not
      subnet_info=await get_subnetwork(interface['namespace'], interface['name'])
      networkInterfaces.append(
          {
            "subnetworkRef": {
              "name": interface['name'],
              "namespace": interface['namespace'] 
            }
          }
      )

      # if this is not a wireguard VPN compute instance generate the routing script
      if vpn == False:
        # check if the subnet is in the route list, if so delete that route from the list
        logger.debug(subnet_info)
        # get the subnet address and strip last digits
        subnet_cidr=subnet_info.get('spec').get('ipCidrRange')[:-5]
        logger.debug("subnet cidr %s", subnet_cidr)

        if subnet_cidr in routes:
          # if the subnet addres is in the route list then set the router_address to subnet gateway
          router_address = subnet_cidr+".1"
          logger.debug("router=%s",router_address)
          # remove the subnet_cidr from routes
          routes.remove(subnet_cidr)
          logger.debug("routes=%s",routes)

    # build the route script
    if router_address is not None:
      for route in routes:
        route_script=route_script+f"sudo ip route add {route}.0/24 via {router_address};"
    logger.debug("Route script = %s", route_script)

  machineType="e2-standard-2"
  # select the machinetype based on the number of interfaces, there must be the same or more number of cores 
  # than the number of NICs
  if len(networkInterfaces)>2:
    machineType="e2-highcpu-4"

  # build out labels
  labels = {}
  if monitor:
    labels["monitor"]="yes"
  if graph:
    labels["graph"] = "true"

  crd_manifest={
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeInstance",
    "metadata": {
      "annotations": {
        "cnrm.cloud.google.com/allow-stopping-for-update": "true",
      },
      "labels": labels,
      "name": vm_name,
      "namespace": namespace
    },
    "spec": {
      "machineType": machineType,
      "zone": zone,
      "bootDisk": {
        "initializeParams": {
          "size": 50,
          "type": "pd-ssd",
          "sourceImageRef": {
            "external": f"ubuntu-os-cloud/{release}"
          },
        },
      },
      "networkInterface": networkInterfaces,
      "canIpForward": True,
      "metadataStartupScript": f"sudo apt-get update; sudo apt-get install -yq python3-pip;{route_script}",
      "metadata": [
        { 
          "key": "ssh-keys",
          "value": f"{google_user}:{google_ssh_pub}"
        }
      ]
    }
  }

  # update manifest with parent child relationship
  kopf.adopt(crd_manifest)
  if parent_name is not None:
    kopf.label(crd_manifest, labels={'kex-parent-name': parent_name})

  try:
    result = compute_api.create(crd_manifest)
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 422:
      raise kopf.PermanentError("Unprocessable entity.")
    elif e.status == 409:
      logger.info("VM %s already exists - skipping", vm_name)
    else:
      logger.debug(e)

########################################################################
# Get ComputeInstance
########################################################################
async def get_compute(namespace, vm_name):
  logger.debug("Get compute %s in ns %s", vm_name, namespace)
  compute_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeInstance")
  try:
    result = compute_api.get(namespace=namespace, name=vm_name)
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 422:
      raise kopf.PermanentError("Unprocessable entity.")
    if e.status == 404:
      return None

########################################################################
# Create External ComputeAddress
########################################################################
async def create_external_ip(namespace, name, region, graph=True):
  logger.debug("Create external ip")
  network_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeAddress")

  # build out labels
  labels = {}
  if graph:
    labels["graph"] = "true"

  crd_manifest= {
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeAddress",
    "metadata": {
      "name": f"{name}",
      "namespace": namespace,
      "labels": labels,
      "annotations": {
        "configmanagement.gke.io/managed": "disabled"
      }
    },
    "spec": {
      "addressType": "EXTERNAL",
      "description": f"{name} external address",
      "location": region
    }
  }
  
  # update manifest to be child of parent object
  kopf.adopt(crd_manifest)
  logger.debug(json.dumps(crd_manifest, indent=4))

  try:
    result = network_api.create(crd_manifest)
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 409:
      logger.debug("Already exists - skipping")
    else:
      logger.debug(e)

########################################################################
# Get ComputeAddress IP address
########################################################################
async def get_ip_address(namespace, name, api_client=None):
  logger.debug("Getting external ip address")

  client = None
  if api_client is None:
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  else:
    client = kubernetes.dynamic.DynamicClient(api_client)

  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeAddress",
  )
  try:
    result = network_api.get(name=name, namespace=namespace)
    if result.get('spec') is not None:
      if result.get('spec')['address'] is not None:
        return result.get('spec').get('address')
      else:
        raise kopf.TemporaryError(f"Waiting for IP address", 15)

  except kubernetes.client.rest.ApiException as e: 
    if e.status == 404:
      raise kopf.TemporaryError(f"No address {name} found yet")
    else:
      logger.debug(e)
      raise kopf.PermanentError("Something bad happened")

########################################################################
# CreateRoute
########################################################################
async def create_route(namespace, vm_name, source_subnetwork, peer_subnetwork):
  logger.debug("Create route to vm %s from source %s to subnetwork %s", vm_name, source_subnetwork, peer_subnetwork)

  route_ip=None

  # find ip address on vm_name assigned to source_subnetwork_name
  vmresult = await get_compute(namespace, vm_name)
  if vmresult is None:
    raise kopf.TemporaryError("Waiting for VM")

  logger.debug("source %s, target %s", source_subnetwork, peer_subnetwork)

  # check the VM has a network and ip address, if not backoff until it does
  if vmresult.get('spec') is not None:
    logger.debug(vmresult.spec)
    for interface in vmresult.spec['networkInterface']:
      if interface.get('subnetworkRef').get('name') is not None:
        if interface['subnetworkRef']['name']==source_subnetwork['name']:
          if interface.get('networkIpRef') is not None and interface.get('networkIpRef').get('external') is not None:
            route_ip=interface['networkIpRef']['external']
            break

  if route_ip is None:
    raise kopf.TemporaryError("Waiting for VM ip address", 20)

  # find the cidr associated with peer_subnetwork_name
  destresult = await get_subnetwork(peer_subnetwork['namespace'], peer_subnetwork['name'])
  peer_cidr = destresult.spec['ipCidrRange']

  # find the network name from the source subnetwork
  sourceresult = await get_subnetwork(source_subnetwork['namespace'], source_subnetwork['name'])
  sourcenetwork = sourceresult.spec['networkRef']['name']

  network_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeRoute")
  route_name = vm_name+'-'+peer_subnetwork['name']
  crd_manifest= {
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeRoute",
    "metadata": {
      "name": route_name,
      "labels": {
        "graph": "true"
      },
      "annotations": {
        "configmanagement.gke.io/managed": "disabled"
      }
    },
    "spec": {
      "description": f"{vm_name} route",
      "destRange": peer_cidr,
      "networkRef": {
        "name": sourcenetwork, 
        "namespace": source_subnetwork['namespace']
      },
      "priority": 100,
      "nextHopIp": route_ip
      }
    }

  kopf.adopt(crd_manifest)
  logger.debug(json.dumps(crd_manifest, indent=4))

  try:

    result = network_api.create(crd_manifest)
    return result

  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 409:
      logger.info("Route %s already exists - skipping", route_name)
    else:
      logger.debug(e)

########################################################################
# Get the mgmt network ip address on a VM
########################################################################
async def get_ip(namespace, name, networkname="mgmt"):
    logger.debug("getting mgmt ip address")
    # get server
    vm = await get_compute(namespace, name)
    if vm is None or vm.get('spec') is None:
        logger.debug("No VM or VM spec")
        return None

    interfaces = vm.spec.get('networkInterface')

    ip_address=None
    for int in interfaces:
        if int.get('networkRef') is not None:
            if int.get('networkRef').get('external') is not None:
                if networkname in int['networkRef']['external']:
                    ip_address = int['networkIpRef']['external']

    if ip_address is None:
        raise kopf.TemporaryError("could not find ip address", 15)
    else:
        logger.debug("Found mgmt ip address %s", ip_address)

    return ip_address

#####################################################################
# Get Compute Subnet Info
#####################################################################
async def get_subnet_info(namespace, subnetname):
  logger.debug("get info for subnet %s in ns %s", subnetname, namespace)
  network_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeSubnetwork")
  try:
    result = network_api.get(name=subnetname, namespace=namespace)
    conditions = result.get('status').get('conditions')
    if conditions[-1].get('reason') != "UpToDate":
        raise kopf.TemporaryError("Waiting for subnet to come up")
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 404:
        raise kopf.TemporaryError(f"No subnet {subnetname} found yet. Waiting...")

#####################################################################
# Get Compute Instance Info
#####################################################################
async def get_vm_info(namespace, vmname):
  logger.debug("get info for vm %s", vmname)
  compute_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeInstance")
  try:
    result = compute_api.get(name=vmname, namespace=namespace)
    status = result.get('status')
    if status.get('currentStatus') != "RUNNING":
      raise kopf.TemporaryError("Waiting for VM to come up")
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 404:
      raise kopf.TemporaryError(f"No VM {vmname} found yet")
