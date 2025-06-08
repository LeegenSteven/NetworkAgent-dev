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
import yaml
import os
import re
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
      logger.debug("Compute network %s already exists - skipping", network_name)
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
      logger.debug("Subnetwork %s already exists - skipping", network_name)
    else:
      logger.debug(e)

########################################################################
# Extract project id from GCP resource url
########################################################################
def get_subnet_name_from_external_link(external_link):
  logger.debug("Get subnet name from external link %s", external_link)
  match = re.search(r"(?<=subnetworks/)([^/]+)", external_link)
  subnet_name = None
  if match:
      subnet_name = match.group(1)
      logger.debug(f"Subnet name found in external link: {subnet_name}")
  else:
      logger.error(f"Subnet name not found in external link: {external_link}")
  return subnet_name


########################################################################
# Extract project id from GCP resource url
########################################################################
def get_net_name_from_external_link(external_link):
  logger.debug("Get net name from external link %s", external_link)
  match = re.search(r"(?<=networks/)([^/]+)", external_link)
  net_name = None
  if match:
      net_name = match.group(1)
      logger.debug(f"Net name found in external link: {net_name}")
  else:
      logger.error(f"Net name not found in external link: {external_link}")
  return net_name

########################################################################
# Extract project id from GCP resource url
########################################################################
def get_project_id_from_external_link(external_link):
  logger.debug("Get project id from external link %s", external_link)
  match = re.search(r"(?<=projects/)([^/]+)", external_link)
  project_id = None
  if match:
      project_id = match.group(1)
      logger.debug(f"Project id found in external link: {project_id}")
  else:
      logger.error(f"Project id not found in external link: {external_link}")
  return project_id

########################################################################
# Get K8s namespaces for a given project
########################################################################
def get_project_namespaces(project_id):
  logger.debug("Get project namespaces for project id %s", project_id)
  k8s_core_v1_api = kubernetes.client.CoreV1Api()
  namespaces = k8s_core_v1_api.list_namespace()
  candidate_namespaces = []
  all_namespace_names = [ns.metadata.name for ns in namespaces.items]

  for ns in namespaces.items:
    annotations = ns.metadata.annotations or {}
    if annotations.get("cnrm.cloud.google.com/project-id") == project_id:
        candidate_namespaces.append(ns.metadata.name)
  logger.debug("Project candidate namespaces: %s", candidate_namespaces)
  return candidate_namespaces

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
      logger.debug("%s in namespace %s not found", name, namespace)
    else:
      logger.error("Exception raised while getting subnetwork %s: %s", name, e.status)
      logger.debug(e)

########################################################################
# Get Subnetwork from a GCP resource linik
########################################################################
async def get_subnetwork_from_external_link(external_link):
  logger.debug("Get compute subnetwork from external link %s", external_link)
  project_id = get_project_id_from_external_link(external_link)
  subnet_name = get_subnet_name_from_external_link(external_link)
  project_namespaces = get_project_namespaces(project_id)

  for ns in project_namespaces:
    logger.debug("Checking namespace %s", ns)
    subnetwork = await get_subnetwork(ns, subnet_name)
    if subnetwork is not None:
      logger.debug("Found a match for subnetwork %s in namespace %s", subnet_name, ns)
    return subnetwork

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
# Get Subnetwork
########################################################################
async def get_network_from_external_link(external_link):
  logger.debug("Get compute network from external link %s", external_link)
  project_id = get_project_id_from_external_link(external_link)
  net_name = get_net_name_from_external_link(external_link)
  project_namespaces = get_project_namespaces(project_id)

  for ns in project_namespaces:
    logger.debug("Checking namespace %s", ns)
    network = await get_subnetwork(ns, net_name)
    if network is not None:
      logger.debug("Found a match for network %s in namespace %s", net_name, ns)
      return network

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
      logger.debug("Route %s already exists - skipping", route_name)
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
    logger.debug(e.status)
    if e.status == 409:
      logger.debug("NAT %s already exists - skipping", nat_name)
    else:
      logger.debug(e)

########################################################################
# Create ComputeInstance
########################################################################
async def create_compute(namespace, parent_name, vm_name, external_ip, interfaces, project, region, zone, vpn=False, monitor=True,release="ubuntu-2204-lts",graph=True):
  logger.debug(f"Create compute vm {vm_name} in ns {namespace}")
  compute_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeInstance")

  # get the google user
  vm_user = os.getenv("GOOGLE_VM_USER")
  if vm_user is None:
    raise kopf.PermanentError("No GOOGLE_VM_USER environment variable.")

  google_ssh_pub=None
  with open(f'{constants.basedir}/google-compute.pub') as f:
    google_ssh_pub=f.read()
  if google_ssh_pub is None:
    raise kopf.PermanentError("No public ssh key found")

  # create the network interfaces for this VM
  networkInterfaces=[]

  # always add the mgmt interface
  address_name = vm_name+"-mgmt-address"
  subnet = f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}/subnetworks/mgmt-subnet"
  net = f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}/networks/mgmt"
  mgmtAddress = await create_internal_ip(namespace, address_name, region, externalsubnet=subnet, graph=graph)

  #if not await get_compute_address(namespace, address_name):
  #  mgmtAddress = await create_internal_ip(namespace, address_name, region, externalsubnet=subnet, graph=True)
  #else:
  #  logger.info(f"ComputeAddress {address_name} in ns {namespace} already exists. Skipping creation.")
  networkInterfaces.append(
    {
      "networkRef": {
        "external": net
      },
      "subnetworkRef": {
        "external": subnet
      },
      "networkIpRef": {
        "kind": "ComputeAddress",
        "name": address_name,
        "namespace": namespace
      }
    })
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
    address_name = vm_name+"-dataplane-address"
    subnet = f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}/subnetworks/dataplane"
    net = f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}/networks/dataplane"
    dataplaneAddress = await create_internal_ip(namespace, address_name, region, externalsubnet=subnet, graph=graph)

    #if not await get_compute_address(namespace, address_name):
    #  dataplaneAddress = await create_internal_ip(namespace, address_name, region, externalsubnet=subnet, graph=True)
    #else:
    #  logger.info(f"ComputeAddress {address_name} in ns {namespace} already exists. Skipping creation.")
    networkInterfaces.append(
      {
        "networkRef": {
          "external": net
        },
        "subnetworkRef": {
          "external": subnet
        },
        "networkIpRef": {
          "kind": "ComputeAddress",
          "name": address_name,
          "namespace": namespace
        }
      })

  # for VNFs we need to build the metadatastartupscript for network routes
  routes = ['10.0.40', '10.0.50', '10.0.60']
  route_script=""
  router_address=None

  # next add the interface to connect to - this equates to ens5 internal nic
  if interfaces is not None:
    for interface in interfaces:
      address_name = vm_name+"-"+interface['name']+"-address"
      # check if the interface has already been added to the network 
      # interface spec, if not then continue
      for ni in networkInterfaces:
        if 'name' in ni['networkIpRef'] and ni['networkIpRef']['name'] == address_name:
          continue

      subnet = f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}/subnetworks/{interface['name']}"
      net = f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}/networks/{interface['name']}"

      networkAddress=None
      networkNamespace=None
      # if this is a VPN, then add the addresses to the vpn namespace, if not then put in interface namespace
      if vpn==True:
        networkAddress = await create_internal_ip(namespace, address_name, region, externalsubnet=subnet, graph=graph)
        networkNamespace = namespace
      else:
        networkAddress = await create_internal_ip(namespace, address_name, region, externalsubnet=subnet, graph=graph)
        networkNamespace = namespace

      networkInterfaces.append(
        {
          "networkRef": {
            "external": net
          },
          "subnetworkRef": {
            "external": subnet
          },
          "networkIpRef": {
            "kind": "ComputeAddress",
            "name": address_name,
            "namespace": networkNamespace
          }
        })
  

      # if this is not a wireguard VPN compute instance generate the routing script
      if vpn == False:
        # check the interface is up, and wait if not
        subnet_info=await get_subnetwork(namespace, interface['name'])
        logger.debug(subnet_info)

        # check if the subnet is in the route list, if so delete that route from the list
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
    labels["monitor"] = "true"
  if graph:
    labels["graph"] = "true"

  crd_manifest = {
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeInstance",
    "metadata": {
      "annotations": {
        "cnrm.cloud.google.com/allow-stopping-for-update": "false",
        "cnrm.cloud.google.com/state-into-spec": "absent",
        "cnrm.cloud.google.com/management-conflict-prevention-policy": "resource",
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
          "size": 200,
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
          "value": f"{vm_user}:{google_ssh_pub}"
        }
      ],
    }
  }

  # update manifest with parent child relationship
  logger.debug(f"ComputeInstance YAML: {yaml.dump(crd_manifest, indent=4)}")
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
      logger.debug("VM %s already exists - skipping", vm_name)
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
    if result is None:
      raise kopf.TemporaryError(f"VM {vm_name} not started yet",20)
    if result.get('status') is None:
      raise kopf.TemporaryError(f"VM {vm_name} - waiting for status",20)

    # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    # FIXME: EXTREMELY DIRTY HACK BECAUSE SOME ComputeInstance get stuck
    # to UpdateFailed trying to stop and update the VM. And it can last forever
    # altough the VM is up and running and ready to use :-
    """status:
    conditions:
    - lastTransitionTime: "2025-04-24T16:59:06Z"
      message: 'Update call failed: error applying desired state: summary: Changing
        the machine_type, min_cpu_platform, service_account, enable_display, shielded_instance_config,
        scheduling.node_affinities or network_interface.[#d].(network/subnetwork/subnetwork_project)
        or advanced_machine_features on a started instance requires stopping it. To
        acknowledge this, please set allow_stopping_for_update = true in your config.
        You can also stop it by setting desired_status = "TERMINATED", but the instance
        will not be restarted after the update.'
      reason: UpdateFailed
      status: "False"
      type: Ready"""
    reason = None
    if 'conditions' in result.get('status'):
      reason = result.get('status').get('conditions')[-1].get('reason')

    currentStatus = result.get('status').get('currentStatus')
    if (currentStatus is not None and currentStatus == "RUNNING") or (reason == "UpdateFailed"):
      return result
    else:
        logger.debug(f"Waiting for vm {vm_name} to come up")
        raise kopf.TemporaryError(f"Waiting for VM {vm_name} to become ready",30)

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
# Create Internal ComputeAddress
########################################################################
async def create_internal_ip(namespace, name, region, externalsubnet=None, subnetworkref=None, address=None, graph=True):
  logger.debug(f"Create internal ip {name} in ns {namespace}")
  network_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeAddress")

  # build out labels
  labels = {}
  if graph:
    labels["graph"] = "true"

  subnetproperty={}
  if externalsubnet is not None:
    subnetproperty['external'] = externalsubnet
  else:
    if subnetworkref is None:
      raise kopf.PermanentError("No subnet reference found.")
    else:
      subnetproperty['name'] = subnetworkref

  crd_manifest= {
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeAddress",
    "metadata": {
      "name": f"{name}",
      "namespace": namespace,
      "labels": labels,
    },
    "spec": {
      "description": f"{name} internal address",
      "location": region,
      "addressType": "INTERNAL",
      "purpose": "GCE_ENDPOINT",
      "subnetworkRef": subnetproperty
    }
  }
  
  if address is not None:
    crd_manifest['spec']['address']=address

  # update manifest to be child of parent object
  logger.debug(f"ComputeAddress description (YAML): {yaml.dump(crd_manifest, indent=2)}")
  kopf.adopt(crd_manifest)

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
# Get ComputeAddress object
########################################################################
async def get_compute_address(namespace, name, api_client=None):
  logger.debug(f"Getting ComputeAddress address {name} in ns {namespace}")

  client = None
  if api_client is None:
    network_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeAddress")
  else:
    client = kubernetes.dynamic.DynamicClient(api_client)
    network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeAddress",
  )
  
  try:
    result = network_api.get(name=name, namespace=namespace)
  except kubernetes.client.rest.ApiException as e: 
    if e.status == 404:
      logger.debug(f"ComputeAddress address {name} in ns {namespace} not found")
      return None
    else:
      logger.debug(e)
      raise kopf.PermanentError("Something bad happened")

  logger.debug(f"ComputeAddress found (YAML): {result}")
  return result

########################################################################
# Get ComputeAddress IP address
########################################################################
async def get_ip_address(namespace, name, api_client=None):
  logger.debug(f"Getting IP address {name} in ns {namespace}")

  compute_address = await get_compute_address(namespace, name)
  if compute_address is None:
    raise kopf.TemporaryError(f"No address {name} found yet")

  if compute_address.get('status') is None:
    raise kopf.TemporaryError("waiting for address to have status", 10)

  conditions = compute_address.get('status').get('conditions')
  if conditions is None:
    raise kopf.TemporaryError("waiting for address to have conditions", 10)

  if conditions[-1].get('reason') != "UpToDate":
      logger.debug(f"Waiting for address {name} to come up")
      raise kopf.TemporaryError(f"Waiting for address {name} to come up",10)
  
  obs_state = compute_address.get('status').get('observedState')
  if obs_state is None:
      raise kopf.TemporaryError(f"Waiting for address {name} to come up in observedState",10)

  return obs_state.get('address')

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
    for interface in vmresult.spec['networkInterface']:
      if interface.get('subnetworkRef').get('external') is not None:
        if source_subnetwork['name'] in interface['subnetworkRef']['external']:
          # get the compute address for this interface
          route_ip = await get_ip_address(interface.get('networkIpRef').get('namespace'), interface.get('networkIpRef').get('name'))
          break

  if route_ip is None:
    raise kopf.TemporaryError("Waiting for VM ip address", 20)

  # find the cidr associated with peer_subnetwork_name
  destresult = await get_subnetwork(namespace, peer_subnetwork['name'])
  peer_cidr = destresult.spec['ipCidrRange']

  # find the network name from the source subnetwork
  sourceresult = await get_subnetwork(namespace, source_subnetwork['name'])
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
        "namespace": namespace
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
      logger.info("Route %s already exists - skipping creation", route_name)
    else:
      logger.debug(e)

########################################################################
# Get the network ip address for a VM named name in ns namespace and
# for network networkname
########################################################################
async def get_ip(namespace, name, networkname="mgmt"):
    logger.debug(f"Getting IP address of VM {name} in ns {namespace} for network {networkname}")

    # get server
    vm = await get_compute(namespace, name)
    interfaces = vm.spec.get('networkInterface')

    ip_address=None
    for int in interfaces:
        if int.get('networkRef') is not None:
            if int.get('networkRef')['external'] is not None:
              if networkname in int.get('networkRef')['external']:
                ip_name = int.get('networkIpRef')['name']
                ip_ns = int.get('networkIpRef')['namespace']
                ip_address = await get_ip_address(ip_ns, ip_name)

    if ip_address is None:
        logger.error(f"Could not find IP address in network {networkname} for VM {name}. Temporary error. Waiting...")
        raise kopf.TemporaryError("could not find ip address", 15)
    else:
        logger.debug(f"{networkname} IP address for VM {name} is {ip_address}")
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
        logger.debug(f"Waiting for subnet {subnetname} to come up")
        raise kopf.TemporaryError("Waiting for subnet to come up")
    else:
      logger.debug(f"Subnet {subnetname} is now up and running")
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 404:
        logger.error(f"No subnet {subnetname} found yet. Temporary error. Waiting...")
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
      logger.debug(f"Waiting for VM {vmname} to come up")
      raise kopf.TemporaryError(f"Waiting for VM {vmname} to come up")
    else:
      logger.debug(f"VM {vmname} is now up and running")
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 404:
      logger.error(f"No VM {vmname} found yet. Temporary error. Waiting...")
      raise kopf.TemporaryError(f"No VM {vmname} found yet")
