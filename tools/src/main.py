import logging
import kubernetes
from utils.k8s import login
from pathlib import Path
from connexion import FlaskApp
# from connexion.options import SwaggerUIOptions

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)

# options = SwaggerUIOptions(swagger_ui_path="/docs")
# app = FlaskApp(__name__, swagger_ui_options=options)
# app.add_api("openapi.yaml",swagger_ui_options=options)
app = FlaskApp(__name__)
app.add_api("openapi.yaml")

######################################################################
# Get existing customer locations
######################################################################
def getCustomerLocations(name):
    logger.info("Getting locations for customer %s", name )

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

    customername = payload['customername']
    servicename = payload['servicename']
    sites = payload['locations']

    login()
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())

    try:
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="PointToPointService",
        )
        crd_manifest= { 
            "apiVersion": "google.dev/v1",
            "kind": "PointToPointService",
            "metadata": {
                "name": servicename,
                "namespace": "automation",
                "labels": {
                    "customer": customername
                },
            },
            "spec": {
                "interfaces": sites
            }
        }
        result = network_api.create(crd_manifest)

        return {"result": str(result)}, 201
    except kubernetes.client.rest.ApiException as e: 
        logger.info(e.status)
        logger.debug(e)
        if e.status == 409:
            logger.info("Already exists - skipping")
            return {}, 409

######################################################################
# Start the server and load routes
######################################################################
if __name__ == "__main__":

    # start the server   
    app.run(f"{Path(__file__).stem}:app", host='0.0.0.0', port=8080)
