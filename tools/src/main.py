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

        if len(locations)==0:
            return {}, 404

        return locations
    except kubernetes.ResourceNotFoundError:
        return {}, 404

######################################################################
# Get a list of Service Definitions
######################################################################
def getServiceDefinitions():
    logger.info("Getting Service definitions")

    import json

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
            services.append(item.to_dict())

        if len(services)==0:
            return {}, 404

        return services
    except kubernetes.dynamic.exceptions.ResourceNotFoundError:
        return {}, 404

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
        services_list=[]

        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        service_descriptors=network_api.get(label_selector="type=connectivityservice")
        for item in service_descriptors.items:
            svc_api = client.resources.get(
                kind=item['spec']['names']['kind'],
                api_version=item['spec']['group']+'/'+item['spec']['versions'][0]['name'],
            )

            services=svc_api.get(label_selector=f"customer={name}")
                
            for item in services.items:
                logger.debug(item)
                svc= {
                    'customer': item['metadata']['labels']['customer'],
                    'kind': item['kind'],
                    'servicename': item['metadata']['name'],
                    'resources': getServiceStatus(client, item['status']['service_resources']),
                }
                services_list.append(svc)

        if len(services_list)==0:
            return {}, 404

        return services_list, 200

    except  kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return {}, 404
        else:
            logger.info(e)

def getServiceStatus(client, resources):
    logger.info("get status for service")
    resources_status=[]

    for resource in resources:
        status_message = getResourceStatus(client, resource['api_version'], resource['kind'], resource['name'])
        status = {'kind': resource['kind'], 'name': resource['name'], 'status': status_message }
        resources_status.append(status)

    return resources_status

def getResourceStatus(client, api_version, kind, name):
    network_api = client.resources.get(
        api_version=api_version, 
        kind=kind,
    )
    resource=network_api.get(namespace="automation", name = name)
    message = resource['status']['conditions'][0]['message']

    return message

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
