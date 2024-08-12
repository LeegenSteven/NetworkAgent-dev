import logging
from services.sitetosite.lifecycle_tasks import *
import uuid
from utils.keys import WgKey

logger = logging.getLogger(__name__)

##########################################
# Create a new PTP VPN
##########################################
@kopf.on.create('pointtopointservice')
async def service_resources(spec, name, namespace, logger, **kwargs):
  logger.info(f"Create pointtopoint service handler is called with spec: {spec}")

  if len(spec.get('interfaces'))!=2:
      raise kopf.PermanentError("Two interfaces must be provided.")

  # build the a and b end variables
  aend=spec.get('interfaces')[0]
  bend=spec.get('interfaces')[1]

  nameUUID = str(uuid.uuid4())[:8]

  akey = WgKey().to_dict()
  bkey = WgKey().to_dict()

  site1 = await create_site(aend, bend, "10.0.10.0/24", "192.168.1.1", nameUUID, akey, bkey)
  site2 = await create_site(bend, aend, "10.0.11.0/24", "192.168.1.2", nameUUID, bkey, akey)

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
    ] 
  
