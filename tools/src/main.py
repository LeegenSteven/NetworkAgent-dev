import logging
import kubernetes
from utils.k8s import login
from pathlib import Path
from connexion import FlaskApp

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)

app = FlaskApp(__name__)
app.add_api("openapi.yaml")

######################################################################
# Get existing customer locations
######################################################################
def getCustomerLocations(name):
    logger.info("Getting locations for customer %s", name )

    if name is None:
        return {}, 400

    login()
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())

    try:
        network_api = client.resources.get(
            api_version="compute.cnrm.cloud.google.com/v1beta1", 
            kind="ComputeSubnetwork",
        )
        result=network_api.get(label_selector=f"customer={name}")
        locations=[]
        for item in result.items:
            logger.info(item)
            location = {
                'name': item['metadata']['name'],
                'description': item['spec']['description'],
                'cidr': item['spec']['ipCidrRange']
            }
            locations.append(location)

        return locations
    except kubernetes.ResourceNotFoundError:
        return {"result": []}

######################################################################
# Get a list of Service Definitions
######################################################################
def getServiceDefinitions():
    logger.info("Getting Service definitions")

    login()
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())

    try:
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        items=network_api.get(label_selector="type=connectivityservice")
        services=[]
        for item in items.items:
            logger.info(item)
            services.append(str(item))

        return services
    except kubernetes.dynamic.exceptions.ResourceNotFoundError:
        return []

######################################################################
# Get existing connectivity services
######################################################################
def getServices(name):
    logger.info("finding service instances for %s", name)

    if name is None:
        return {}, 400

    login()
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())

    try:
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="PointToPointService",
        )
        items=network_api.get(label_selector=f"customer={name}")
        services=[]
        for item in items.items:
            logger.info(item)
            service = {
                'customer': item['metadata']['labels']['customer'],
                'servicename': item['metadata']['name'],
                'locations': item['spec']['interfaces'],
            }
            services.append(service)

        return services
    except kubernetes.dynamic.exceptions.ResourceNotFoundError:
        return []

######################################################################
# Create a new connectivity service
######################################################################
def createService(payload):
    logger.info("Create a new Service from %s", str(payload))

    if 'customerName' not in payload or 'serviceInfo' not in payload or 'apiVersion' not in payload['serviceInfo'] or 'spec' not in payload['serviceInfo'] or 'kind' not in payload['serviceInfo']:
        return {}, 400

    customerName = payload['customerName']
    serviceKind = payload['serviceInfo']['kind']
    serviceApiVersion = payload['serviceInfo']['apiVersion']
    serviceSpec = payload['serviceInfo']['spec']

    login()
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    name = customerName.lower()

    try:
        network_api = client.resources.get(
            api_version=serviceApiVersion, 
            kind=serviceKind,
        )
        crd_manifest= { 
            "apiVersion": serviceApiVersion,
            "kind": serviceKind,
            "metadata": {
                "name": name,
                "namespace": "automation",
                "labels": {
                    "customer": customerName
                },
            },
            "spec": serviceSpec
        }
        result = network_api.create(crd_manifest)

        return {"result": str(result)}, 201
    except kubernetes.client.rest.ApiException as e: 
        logger.info(e.status)
        logger.debug(e)
        if e.status == 409:
            logger.info("Already exists - skipping")
            return {}, 409
        else:
            logger.info(e)
            return {}, e.status

######################################################################
# Delete an existing connectivity service
######################################################################
def deleteService(name, kind):
    if name is None or kind is None:
        return {}, 400

    login()
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())

    try:

        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind=kind,
        )
        network_api.delete(name=name, namespace="automation")
        return {}, 200

    except kubernetes.client.rest.ApiException as e: 
        logger.info(e.status)
        logger.debug(e)
        if e.status == 404:
            logger.info("No service found")
            return {}, 404
        else:
            logger.info(e)
            return {}, e.status

######################################################################
# Start the server and load routes
######################################################################
if __name__ == "__main__":

    # start the server   
    app.run(f"{Path(__file__).stem}:app", host='0.0.0.0', port=8080)
