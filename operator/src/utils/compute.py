import logging
import kubernetes
import kopf
import json
import os
import utils.constants as constants

logger = logging.getLogger(__name__)

########################################################################
# Create ComputeNetwork
########################################################################
async def create_network(network_name):
  logger.debug("Create compute network %s", network_name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeNetwork",
  )

  crd_manifest={
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeNetwork",
    "metadata": {
      "name": network_name,
      "namespace": "automation"
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
    logger.info(e.status)
    if e.status == 409:
      logger.info("already exists - skipping")
    else:
      logger.debug(e)

########################################################################
# Delete ComputeNetwork
########################################################################
async def delete_network(network_name):
  logger.debug("Delete compute network %s", network_name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeNetwork",
  )

  try: 
    result = network_api.delete(name=network_name, body={}, namespace="automation")
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    logger.debug(e)

########################################################################
# Create ComputeSubNetwork
########################################################################
async def create_subnetwork(network_name, subnet_name, cidr, region):
  logger.debug("Create compute subnetwork")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeSubnetwork",
  )

  crd_manifest= { 
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeSubnetwork",
    "metadata": {
      "name": subnet_name,
      "namespace": "automation"
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
      logger.info("Already exists - skipping")
    else:
      logger.debug(e)

########################################################################
# Get Subnetwork
########################################################################
async def get_subnetwork(name):
  logger.debug("Get compute subnetwork %s", name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeSubnetwork",
  )
  try:
    result = network_api.get(namespace="automation", name=name)
    return result
  except kubernetes.client.rest.ApiException as e:
    logger.debug(e)

########################################################################
# Create ComputeRouter
########################################################################
async def create_router(network_name, region):
  logger.debug("Create Router")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeRouter",
  )

  crd_manifest={
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeRouter",
    "metadata":{
      "name": f"{network_name}-router",
      "namespace": "automation"
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
      logger.debug("Already exists - skipping")
    else:
      logger.debug(e)

########################################################################
# ComputeRouterNAT
########################################################################
async def create_nat(network_name, region):
  logger.debug("Create NAT")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeRouterNAT",
  )

  crd_manifest={
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeRouterNAT",
    "metadata": {
      "name": f"{network_name}-nat",
      "namespace": "automation"
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
      logger.info("Already exists - skipping")
    else:
      logger.debug(e)

########################################################################
# Create ComputeInstance
########################################################################
async def create_compute(parent_name, vm_name, external_ip, interface, project, region, zone, vpn=False, monitor=True):
  logger.debug("Create compute %s", vm_name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeInstance",
  )

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
            "name": "dataplane"
          }
        }
    )

  # next add the interface to connect to - this equates to ens5 internal nic
  if interface is not None:
    networkInterfaces.append(
        {
          "subnetworkRef": {
            "name": interface
          }
        }
    )

  machineType="e2-standard-2"
  # select the machinetype based on the number of interfaces, there must be the same or more number of cores 
  # than the number of NICs
  if len(networkInterfaces)>2:
    machineType="e2-highcpu-4"

  # build out labels
  labels = {}
  if monitor:
    labels["monitor"]="yes"

  crd_manifest={
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeInstance",
    "metadata": {
      "annotations": {
        "cnrm.cloud.google.com/allow-stopping-for-update": "true"
      },
      "labels": labels,
      "name": vm_name,
      "namespace": "automation"
    },
    "spec": {
      "machineType": machineType,
      "zone": zone,
      "bootDisk": {
        "initializeParams": {
          "size": 50,
          "type": "pd-ssd",
          "sourceImageRef": {
            "external": "ubuntu-os-cloud/ubuntu-2204-lts"
          },
        },
      },
      "networkInterface": networkInterfaces,
      "canIpForward": True,
      "metadataStartupScript": "sudo apt-get update; sudo apt-get install -yq python3-pip",
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
    result = network_api.create(crd_manifest)
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 422:
      raise kopf.PermanentError("Unprocessable entity.")
    elif e.status == 409:
      logger.debug("already exists - skipping")
    else:
      logger.debug(e)

########################################################################
# Get ComputeInstance
########################################################################
async def get_compute(vm_name):
  logger.debug("Get compute %s", vm_name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeInstance",
  )

  try:

    result = network_api.get(namespace="automation", name=vm_name)
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
async def create_external_ip(name, region):
  logger.debug(f"Create external ip {name}")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeAddress",
  )

  crd_manifest= {
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeAddress",
    "metadata": {
      "name": f"{name}",
      "namespace": "automation"
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
# Get ComputeAddress external IP address
########################################################################
async def get_external_ip_address(name):
  logger.debug("Getting external ip address")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeAddress",
  )
  try:
    result = network_api.get(name=name, namespace="automation")
    if result.get('spec') is not None:
      if result.get('spec')['address'] is not None:
        return result.get('spec').get('address')
      else:
        raise kopf.TemporaryError(f"Waiting for external IP address", 15)

  except kubernetes.client.rest.ApiException as e: 
    if e.status == 404:
      raise kopf.TemporaryError(f"No address {name} found yet")
    else:
      logger.debug(e)
      raise kopf.PermanentError("Something bad happened")

########################################################################
# CreateRoute
########################################################################
async def create_route(vm_name, source_subnetwork_name, peer_subnetwork_name):
  logger.debug("Create route to vm %s from source %s to subnetwork %s", vm_name, source_subnetwork_name, peer_subnetwork_name)

  route_ip=None

  # find ip address on vm_name assigned to source_subnetwork_name
  vmresult = await get_compute(vm_name)
  if vmresult is None:
    raise kopf.TemporaryError("Waiting for VM")

  logger.debug("source %s, target %s", source_subnetwork_name, peer_subnetwork_name)

  # check the VM has a network and ip address, if not backoff until it does
  if vmresult.get('spec') is not None:
    logger.debug(vmresult.spec)
    for interface in vmresult.spec['networkInterface']:
      if interface.get('subnetworkRef').get('name') is not None:
        if interface['subnetworkRef']['name']==source_subnetwork_name:
          if interface.get('networkIpRef') is not None and interface.get('networkIpRef').get('external') is not None:
            route_ip=interface['networkIpRef']['external']
            break

  if route_ip is None:
    raise kopf.TemporaryError("Waiting for VM ip address", 20)

  # find the cidr associated with peer_subnetwork_name
  destresult = await get_subnetwork(peer_subnetwork_name)
  peer_cidr = destresult.spec['ipCidrRange']

  # find the network name from the source subnetwork
  sourceresult = await get_subnetwork(source_subnetwork_name)
  sourcenetwork = sourceresult.spec['networkRef']['name']

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeRoute",
  )

  crd_manifest= {
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeRoute",
    "metadata": {
      "name": vm_name+'-'+peer_subnetwork_name
    },
    "spec": {
      "description": f"{vm_name} route",
      "destRange": peer_cidr,
      "networkRef": {
        "name": sourcenetwork
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
      logger.debug("Already exists - skipping")
    else:
      logger.debug(e)

########################################################################
# Get the mgmt network ip address on a VM
########################################################################
async def get_ip(name, networkname="mgmt"):
    logger.debug("getting mgmt ip address")
    # get server
    vm = await get_compute(name)
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
        logger.debug("found mgmt ip address %s", ip_address)

    return ip_address

#####################################################################
# Get Compute Subnet Info
#####################################################################
async def get_subnet_info(subnetname):
    logger.debug("get info for subnet %s", subnetname)

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    network_api = client.resources.get(
        api_version="compute.cnrm.cloud.google.com/v1beta1", 
        kind="ComputeSubnetwork",
    )
    try:
        result = network_api.get(name=subnetname, namespace="automation")
        conditions = result.get('status').get('conditions')
        if conditions[-1].get('reason') != "UpToDate":
            raise kopf.TemporaryError("Waiting for subnet to come up")
        return result
    except kubernetes.client.rest.ApiException as e: 
        logger.info(e.status)
        if e.status == 404:
            raise kopf.TemporaryError(f"No subnet {subnetname} found yet")

#####################################################################
# Get Compute Instance Info
#####################################################################
async def get_vm_info(vmname):
    logger.debug("get info for vm %s", vmname)

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    network_api = client.resources.get(
        api_version="compute.cnrm.cloud.google.com/v1beta1", 
        kind="ComputeInstance",
    )

    try:
        result = network_api.get(name=vmname, namespace="automation")
        status = result.get('status')
        if status.get('currentStatus') != "RUNNING":
            raise kopf.TemporaryError("Waiting for VM to come up")
        return result
    except kubernetes.client.rest.ApiException as e: 
        logger.debug(e.status)
        if e.status == 404:
            raise kopf.TemporaryError(f"No VM {vmname} found yet")
