import logging
import kubernetes
import kopf

logger = logging.getLogger(__name__)

########################################################################
# ComputeNetwork
########################################################################
async def create_network(network_name):
  logger.info("Create compute network %s", network_name)

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

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    if e.status == 409:
      # raise kopf.PermanentError("Conflict error, resource already exists.")
      logger.info("already exists - skipping")


async def delete_network(network_name):
  logger.info("Delete compute network %s", network_name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeNetwork",
  )

  try: 
    result = network_api.delete(name=network_name, body={}, namespace="automation")
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)

########################################################################
# ComputeSubNetwork
########################################################################
async def create_subnetwork(network_name, subnet_name, cidr, region):
  logger.info("Create compute subnetwork")

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

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    if e.status == 409:
      # raise kopf.PermanentError("Conflict error, resource already exists.")
      logger.info("Already exists - skipping")

########################################################################
# ComputeRouter
########################################################################
async def create_router(network_name, region):
  logger.info("Create Router")

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

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    if e.status == 409:
      # raise kopf.PermanentError("Conflict error, resource already exists.")
      logger.info("Already exists - skipping")

########################################################################
# ComputeRouterNAT
########################################################################
async def create_nat(network_name, region):
  logger.info("Create NAT")

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

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    if e.status == 409:
      # raise kopf.PermanentError("Conflict error, resource already exists.")
      logger.info("Already exists - skipping")

########################################################################
# ComputeFirewall
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
      ],
      "targetTags": [
        "wireguard"
      ]
    }
  }
  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    if e.status == 409:
      # raise kopf.PermanentError("Conflict error, resource already exists.")
      logger.info("Already exists - skipping")

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
      ],
      "targetTags": [
        "ssh"
      ]
    }
  }

  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    if e.status == 409:
      # raise kopf.PermanentError("Conflict error, resource already exists.")
      logger.info("Already exists - skipping")

########################################################################
# ComputeInstance
########################################################################
async def create_compute(vm_name, subnet_name, interface, project, region, zone, mgmtsubnetname):
  logger.info("Create compute %s", vm_name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeInstance",
  )

  crd_manifest={
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeInstance",
    "metadata": {
      "annotations": {
        "cnrm.cloud.google.com/allow-stopping-for-update": "true"
      },
      "name": vm_name,
      "namespace": "automation"
    },
    "spec": {
      "machineType": "e2-highcpu-4",
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
      "networkInterface": [
        {
          "subnetworkRef": {
            "name": subnet_name
          },
          "accessConfig": [
            {
              "natIpRef": {
                "name": f"{vm_name}-ip"
              }
            }
          ]
        },
        {
          "subnetworkRef": {
            "name": interface
          }
        },
        {
          "subnetworkRef": {
            "external": f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}/subnetworks/{mgmtsubnetname}"
          }
        }
      ],
      "canIpForward": True,
      "metadataStartupScript": "sudo apt-get update; sudo apt-get install -yq python3-pip",
      "metadata": [
        { 
          "key": "ssh-keys",
          "value": "admin_briannaughton_altostrat_co:ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIN57YanONs2q/Eor4Tjv1NRhBGrB59vPFAMZbvlOVtkX briannaughton"
        }
      ],
      "tags": [
        "ssh",
        "wireguard"
      ]
    }
  }

  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    if e.status == 422:
      raise kopf.PermanentError("Unprocessable entity.")
    if e.status == 409:
      # raise kopf.PermanentError("Conflict error, resource already exists.")
      logger.info("already exists - skipping")


########################################################################
# ComputeAddress
########################################################################
async def create_external_ip(vmname, region):
  logger.info("Create external ip")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeAddress",
  )

  crd_manifest= {
    "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
    "kind": "ComputeAddress",
    "metadata": {
      "name": f"{vmname}-ip",
      "namespace": "automation"
    },
    "spec": {
      "addressType": "EXTERNAL",
      "description": "external address",
      "location": region
    }
  }

  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    if e.status == 409:
      # raise kopf.PermanentError("Conflict error, resource already exists.")
      logger.info("Already exists - skipping")


########################################################################
# WireguardAppliance
########################################################################
async def create_vpn_edge(vpn_name, vm_name, tunnel_subnet, tunnel_ip, peer_interface, peer_vm_name):
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
      "peer": peer_vm_name
    }
  }

  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    if e.status == 409:
      raise kopf.PermanentError("Conflict error, resource already exists.")
