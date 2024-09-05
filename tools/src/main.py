import logging
import kubernetes
from utils.k8s import get_client, get_credentials
from pathlib import Path
from connexion import FlaskApp
import os
import json
from google.cloud import bigquery
import uuid

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

    client = kubernetes.dynamic.DynamicClient(get_client())

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
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return {}, 404
        else:
            logger.debug(e)

######################################################################
# Get existing customer applications
######################################################################
def getCustomerApplications(name):
    logger.info("Getting applications for customer %s", name )

    if name is None:
        return {}, 400

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        network_api = client.resources.get(
            api_version="compute.cnrm.cloud.google.com/v1beta1", 
            kind="ComputeInstance",
        )
        result=network_api.get(label_selector=f"customer={name}")
        apps=[]
        for item in result.items:
            logger.info(item)
            app = {
                'name': item['metadata']['name'],
            }
            apps.append(app)

        if len(apps)==0:
            return {}, 404

        return apps
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return {}, 404
        else:
            logger.debug(e)

######################################################################
# Get a list of Service Definitions
######################################################################
def getServiceDefinitions():
    logger.info("Getting Service definitions")

    client = kubernetes.dynamic.DynamicClient(get_client())

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
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return {}, 404
        else:
            logger.debug(e)

######################################################################
# Get existing connectivity services
######################################################################
def getServices(name):
    logger.info("finding service instances for %s", name)

    if name is None:
        return {}, 400

    client = kubernetes.dynamic.DynamicClient(get_client())

    # need to flatten string or k8s freaks out
    customerName = name.lower()

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

            services=svc_api.get(label_selector=f"customer={customerName}")

            for item in services.items:
                logger.debug(item)
                if item.get('status') is None or item.get('status').get('pointtopoint') is None:
                    svc={"servicename": item['metadata']['name'],
                         "status": "Starting"}
                else:
                    svc= {
                        'servicename': item['metadata']['name'],
                        'status': item.get('status').get('pointtopoint').get('status'),
                        'vnfs': item.get('status').get('pointtopoint').get('edges')
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

    client = kubernetes.dynamic.DynamicClient(get_client())
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
                "name": name+str(uuid.uuid4())[:8],
                "namespace": "automation",
                "labels": {
                    "customer": name
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

    client = kubernetes.dynamic.DynamicClient(get_client())

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
# Create a new connectivity test
######################################################################
def createTest(payload):
    logger.info("Create a new Test from %s", str(payload))

    name = payload['name']
    virtualmachines=payload['virtualmachines']

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="ConnectivityTest",
        )
        crd_manifest= { 
            "apiVersion": "google.dev/v1",
            "kind": "ConnectivityTest",
            "metadata": {
                "name": name,
                "namespace": "automation",
            },
            "spec": {
                "virtualmachines": virtualmachines
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
        else:
            logger.info(e)
            return {}, e.status

######################################################################
# Delete a connectivity test
######################################################################
def deleteTest(name):
    if name is None:
        return {}, 400

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:

        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="ConnectivityTest",
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
# Get Service Performance Metrics
######################################################################
def getServicePerformanceMetrics(name, period):
    logger.info("Getting metrics for %s for the last %s mins", name, period)

    client = bigquery.Client(credentials=get_credentials())
    table_id = os.getenv("GOOGLE_PROJECT")+".serviceperformance.serviceperformance"

    results = client.query_and_wait(
        f"""
        SELECT servicename, AVG(receive) as average_receive_total, AVG(sent) as average_sent_total
        FROM (
        SELECT
        JSON_VALUE(data, "$.servicename") as servicename,
        FLOAT64(
            JSON_EXTRACT(data, "$.node_network_receive_bytes_total")
        )  as receive,
        FLOAT64(
            JSON_EXTRACT(data, "$.node_network_transmit_bytes_total")
        )  as sent
        FROM `{table_id}`
        WHERE JSON_VALUE(data, '$.servicename')='{name}' AND publish_time BETWEEN TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL -{period} MINUTE) AND CURRENT_TIMESTAMP()
        )
        GROUP BY servicename
        """
    )

    records = [dict(row) for row in results]
    if len(records)>0:
        return records[0],200
    else:
        return {}, 200

######################################################################
# Start the server and load routes
######################################################################
if __name__ == "__main__":
    
    # start the server
    app.run(f"{Path(__file__).stem}:app", host='0.0.0.0', port=int(os.getenv("PORT","8080")))
