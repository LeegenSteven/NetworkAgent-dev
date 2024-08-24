import logging
from services.sitetosite.lifecycle_tasks import *
import uuid
from utils.keys import WgKey

logger = logging.getLogger(__name__)

services={}

##########################################
# Create a new PTP VPN
##########################################
@kopf.on.create('pointtopointservice')
async def service_resources(spec, name, namespace, logger, **kwargs):
  logger.info(f"Create pointtopoint service {name} with spec: {spec}")

  global services

  if len(spec.get('interfaces'))!=2:
      raise kopf.PermanentError("Two interfaces must be provided.")

  # build the a and b end variables
  aend=spec.get('interfaces')[0]
  bend=spec.get('interfaces')[1]

  # check that aend and bend are valid computesubnetworks
  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="compute.cnrm.cloud.google.com/v1beta1", 
      kind="ComputeSubnetwork",
  )
  try:
    network_api.get(namespace="automation", name=aend)
    network_api.get(namespace="automation", name=bend)
  except:
    raise kopf.PermanentError("interfaces not found")

  # Create uuid and keys for this service instance
  if name not in services:
    logger.info("creating new keys for %s", name)
    newuuid = str(uuid.uuid4())[:8]
    services[name]={}
    services[name]['uuid'] = newuuid
    services[name]['akey'] = WgKey().to_dict()
    services[name]['bkey'] = WgKey().to_dict()
  else:
    logger.info("Using existing keys")

  logger.info(services)

  # Create the site infra
  site1 = await create_site(aend, bend, "192.168.1.1", services[name]['uuid'], services[name]['akey'], services[name]['bkey'])
  site2 = await create_site(bend, aend, "192.168.1.2", services[name]['uuid'], services[name]['bkey'], services[name]['akey'])

  asitevm=aend+'-vpn-'+services[name]['uuid']
  bsitevm=bend+'-vpn-'+services[name]['uuid']

  # Create the static routes to vpn tunnels
  await create_route(aend+'-vpn-'+services[name]['uuid'], aend, bend)
  await create_route(bend+'-vpn-'+services[name]['uuid'], bend, aend)

  # remove the services from the list so a service with the same name can be readded down the line
  logger.info("remove service from list")

  return [
      #  {
      #   'kind': 'Wireguard',
      #   'api_version': "google.dev/v1",
      #   'name': avars['vmname']
      #  },     
       {
        'kind': 'ComputeInstance',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': asitevm
       },
       {
        'kind': 'ComputeNetwork',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': asitevm
       },
       {
        'kind': 'ComputeSubnetwork',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': asitevm
       },
       {
        'kind': 'ComputeRouter',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': f"{asitevm}-router"
       },
       {
        'kind': 'ComputeRouterNAT',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': f"{asitevm}-nat"
       },
       {
        'kind': 'ComputeRoute',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': asitevm
       },
      #  {
      #   'kind': 'Wireguard',
      #   'api_version': "google.dev/v1",
      #   'name': bvars['vmname']
      #  },
       {
        'kind': 'ComputeInstance',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': bsitevm
       },
       {
        'kind': 'ComputeNetwork',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': bsitevm
       },
       {
        'kind': 'ComputeSubnetwork',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': bsitevm
       },
       {
        'kind': 'ComputeRouter',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': f"{bsitevm}-router"
       },
       {
        'kind': 'ComputeRouterNAT',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': f"{bsitevm}-nat"
       },
       {
        'kind': 'ComputeRoute',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': bsitevm
       },
    ] 
  
##########################################
# Cleanup a new PTP VPN
##########################################
@kopf.on.delete('pointtopointservice')
async def delete_service_resources(spec, name, namespace, logger, **kwargs):
  logger.info(f"Delete pointtopoint service {name} with spec: {spec}")

  global services
  if services.get(name) is not None:
    logger.info("deleting keys %s",str(services[name]))
    del services[name]
