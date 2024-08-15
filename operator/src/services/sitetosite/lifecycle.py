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

  # TODO check that aend and bend are valid computesubnetworks

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
  site1 = await create_site(aend, bend, "10.0.10.0/24", "192.168.1.1", services[name]['uuid'], services[name]['akey'], services[name]['bkey'])
  site2 = await create_site(bend, aend, "10.0.11.0/24", "192.168.1.2", services[name]['uuid'], services[name]['bkey'], services[name]['akey'])

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
        'name': site1['vmname']
       },
       {
        'kind': 'ComputeNetwork',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': site1['vmname']
       },
       {
        'kind': 'ComputeSubnetwork',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': site1['vmname']
       },
       {
        'kind': 'ComputeRouter',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': f"{site1['vmname']}-router"
       },
       {
        'kind': 'ComputeRouterNAT',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': f"{site1['vmname']}-nat"
       },
       {
        'kind': 'ComputeRoute',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': f"{site1['vmname']}"
       },
      #  {
      #   'kind': 'Wireguard',
      #   'api_version': "google.dev/v1",
      #   'name': bvars['vmname']
      #  },
       {
        'kind': 'ComputeInstance',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': site2['vmname']
       },
       {
        'kind': 'ComputeNetwork',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': site2['vmname']
       },
       {
        'kind': 'ComputeSubnetwork',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': site2['vmname']
       },
       {
        'kind': 'ComputeRouter',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': f"{site2['vmname']}-router"
       },
       {
        'kind': 'ComputeRouterNAT',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': f"{site2['vmname']}-nat"
       },
       {
        'kind': 'ComputeRoute',
        'api_version': "compute.cnrm.cloud.google.com/v1beta1",
        'name': f"{site2['vmname']}"
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
