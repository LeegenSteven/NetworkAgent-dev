import kopf
import logging
from utils.compute import *
from resources.wireguard.lifecycle_tasks import *;
from monitor.lifecycle_tasks import *

logger = logging.getLogger(__name__)

@kopf.on.create('google.dev','v1','monitor')
async def monitor(spec, name, namespace, logger, **kwargs):
    logger.debug("Create prometheus monitor")

    await create_compute(namespace, 
                         name,
                         "monitor",
                         None,
                         None, 
                         os.getenv("GOOGLE_PROJECT"),
                         os.getenv("GOOGLE_REGION"),
                         os.getenv("GOOGLE_ZONE"), 
                         monitor=False) # set to false so this VM is not scraped by prometheus

    await run_install()

    if spec.get("nodes") is not None:
        await run_update(spec.get("nodes"))
    else:
        await run_update([])

    return {"status": "Running"}

@kopf.on.update('google.dev','v1','monitor')
async def update_monitor(spec, name, namespace, logger, **kwargs):
    logger.debug("Update monitor with new nodes")

    if spec.get("nodes") is None:
        await run_update([])
    else:
        await run_update(spec.get("nodes"))


######################################################################################################
# Watch for VPN virtual machines being created and update the prometheus scrape config with those node
# exporter endpoint addresses
######################################################################################################
@kopf.on.event('compute.cnrm.cloud.google.com','v1beta1','computeinstances',labels={'monitor': 'yes'})
async def monitorevent(event, body, name, spec, logger, **kwargs):
    logger.debug("Updating VM's to monitor")
    try:
        client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="Monitor",
        )
        monitor = network_api.get(name="monitor", namespace="automation")
        if monitor is None:
            return

        # get the mgmt ip address of the VM
        interfaces = spec.get('networkInterface')
        ip_address=None
        for int in interfaces:
            if int.get('networkRef') is not None:
                if int.get('networkRef').get('external') is not None:
                    if "mgmt" in int['networkRef']['external']:
                        ip_address = int['networkIpRef']['external']

        if ip_address is None:
            raise kopf.TemporaryError("waiting for IP address")
        
        logger.debug("ip address for %s is %s", name, ip_address)

        newmonitor=monitor.to_dict()        
        # get the existing node addresses
        nodes = monitor.get('spec').get('nodes')
        if nodes is None:
            nodes = []

        node_address=ip_address+":9100"
        # if this is a create event then add the ip address
        if event.get('type')=="MODIFIED":
            if node_address not in nodes:
                nodes.append(node_address)
        elif event.get('type')=="DELETED":
            if node_address in nodes:
                nodes.remove(node_address)

        newmonitor['spec']['nodes']=nodes
        logger.debug("new monitor nodes is %s", nodes)

        network_api.patch(body= newmonitor, name="monitor", namespace="automation", content_type='application/merge-patch+json')

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.info("no monitor - skipping")
        else:
            logger.error(e)
