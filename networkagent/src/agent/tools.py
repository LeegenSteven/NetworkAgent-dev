from langchain_core.tools import tool
from typing import Optional, Annotated, List, Dict, Any
from pydantic import BaseModel, Field
import logging
import kubernetes
from utils.k8s import get_client, get_credentials
import os
from google.cloud import bigquery
import uuid
from utils.git_helpers import *
import yaml

logger = logging.getLogger(__name__)

# if GITOPS true then the service deletion / creation
# is performed through the Gitea repository + Config Sync
# Otherwise it is executed directly through K8s apply/delete
GITOPS = True

######################################################################
# Get existing customer locations tool
######################################################################
@tool
def getCustomerLocations(
    name: Annotated[str, "The customer name"]
    )-> str:
    """
    Fetch a list of Customer GCP network locations that can be connected 
    with the available connectivity services

    Returns:
      A list of customer locations or network names in Markdown format.
    """
    logger.info("Getting locations for a customer %s", name )

    if name is None:
        return "You must provide a customer name"

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        network_api = client.resources.get(
            api_version="compute.cnrm.cloud.google.com/v1beta1", 
            kind="ComputeSubnetwork",
        )
        result=network_api.get(label_selector=f"customer={name.lower()}")
        locations=""

        for item in result.items:
            logger.info(item)
            location = f"""
__Network Name:__ {item['metadata']['name']}
* _Description_: {item['spec']['description']}
* _CIDR_: {item['spec']['ipCidrRange']}
            """
            locations = locations + location

        if locations:
             locations = f"**Network Locations for Customer {name}**" + locations
        else:
            locations = f"Customer {name} has no locations"


        return locations
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return "No locations found"
        else:
            logger.debug(e)

######################################################################
# Get existing customer applications
######################################################################
def getCustomerApplications(
    name: Annotated[str, "The customer name"]
    ) -> str:
    """
    Fetch a set of Customer specific IT applications that are attached to each Customer network location

    Returns:
        List of IT application names in Markdown forma
    """
    logger.info("Getting applications for customer %s", name )

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        network_api = client.resources.get(
            api_version="compute.cnrm.cloud.google.com/v1beta1", 
            kind="ComputeInstance",
        )
        result=network_api.get(label_selector=f"customer={name.lower()}")
        apps=f"""
**IT Applications for customer {name}**
"""
        for item in result.items:
            logger.info(item)
            apps=apps+f"""
* {item['metadata']['name']}
"""
        if len(apps)==0:
            return "No IT applications found" 

        return apps
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return "No IT applications found" 
        else:
            logger.debug(e)

######################################################################
# Get a list of Service Definitions
######################################################################
@tool
def getServiceDefinitions()->str:

    """
    Fetch the available network connectivity services that can be instantiated.

    Returns:
        A set of kubernetes custom resource CRDs that can orchestrate a set of connectivity services. 
        Each CRD provides the following information:
        - description of the connectivity service functionality
        - a spec section that has the name of the 'kind' for each connectivity service and an OpenAPI schema describing the information required to instantiate the kind connectivity service.
    """
    logger.info("Getting Service definitions")

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        items=network_api.get(label_selector="type=connectivityservice")
        logger.debug("NW SERVICES ITEMS")
        logger.debug(items)
        services=[]
        for item in items.items:
            services.append(f"{item}")

        if len(services)==0:
            return ""
        else:
            return "\n".join(services)
        
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return ""
        else:
            logger.debug(e)

######################################################################
# Get existing connectivity services
######################################################################
@tool
def getServices(
    name: Annotated[str, "The Customer name"]
    )-> str:
    """
    Fetch the network connectivity service instances that are currently deployed for a specified customer.

    Returns:
        Connectivity service instances and their status in Markdown format
        The current operational status of the service is also provided.
    """
    logger.info("finding service instances for %s", name)

    client = kubernetes.dynamic.DynamicClient(get_client())

    # need to lower case the cutomer name or k8s freaks out
    customerName = name.lower()

    try:
        services_list=""
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        service_descriptors=network_api.get(label_selector="type=connectivityservice")
        for crd in service_descriptors.items:
            svc_api = client.resources.get(
                kind=crd['spec']['names']['kind'],
                api_version=crd['spec']['group']+'/'+crd['spec']['versions'][0]['name'],
            )

            services=svc_api.get(label_selector=f"customer={customerName}")

            for item in services.items:
                logger.debug(item)
                service_description=""
                if item.get('status') is None or item.get('status').get('currentStatus') is None:
                    service_description=f"""
* __Servicename__: {item['metadata']['name']}
  * _Kind_: {crd['spec']['names']['kind']}
  * _Status_: Pending"""
                    services_list=services_list+service_description
                else:
                    service_description=f"""
* __Servicename__: {item['metadata']['name']}
  * _Kind_: {crd['spec']['names']['kind']}
  * _Status_: {item.get('status').get('currentStatus')}
"""
                    services_list=services_list+service_description

        if services_list:
             services_list=f"**Connectivity Service Instances for {name}**" + services_list
        else:
            services_list=f"Customer {name} has no connectivty services currently deployed."

        logger.debug(services_list)
        return services_list

    except  kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return """No services found"""
        else:
            logger.info(e)

######################################################################
# Create a new connectivity service
######################################################################
@tool
def createService(
    customerName: Annotated[str, "customer name is required"],
    serviceKind: Annotated[str, "the kubernetes kind for this service instance"], 
    serviceName: Annotated[str, "the kubernetes name for this service instance"], 
    serviceSpec: Annotated[Dict, "the kubernetes spec object for the new service"]
    ) -> str:
    """
    Tool used to deploy (also called instantiate) a new network connectivity service. 
    The types and specifications of network connectivity services available can be discovered by calling the getServiceDefinitions tool.
    Only call this tool if explicitely stated in the network agent query or question.
    Always ask for an explicit confirmation by yes or no before deleting the service.
    """
    logger.info("Create a new Service for %s from %s", customerName, str(serviceSpec))

    # If the user gave the entire spec block then only keep its
    # content which should be the 'interfaces' description
    if "spec" in serviceSpec.keys():
        serviceSpec = serviceSpec["spec"]
    customerName = customerName.lower()
    if not serviceName:
        serviceName = serviceKind.lower()+str(uuid.uuid4())[:8]

    crd_manifest= { 
        "apiVersion": "google.dev/v1",
        "kind": serviceKind,
        "metadata": {
            "name": serviceName,
            "labels": {
                "customer": customerName,
                "graph": "true"
            },
            "annotations": {
                "client.lifecycle.config.k8s.io/mutation": "ignore"
            }
        },
        "spec": serviceSpec
    }

    crd_manifest_yaml = yaml.dump(crd_manifest, indent=2)
    if GITOPS:
        filename = serviceName+".yaml"
        result = commit_git_file(filename,
                                 f"Deployment of {serviceName}",
                                 crd_manifest_yaml)
        if result:
            return f"service {serviceName} manifest successfully submitted for deployment:\n```yaml\n{crd_manifest_yaml}\n```"
        else:
            return f"service {serviceName} could not be deployed"

    else:
        client = kubernetes.dynamic.DynamicClient(get_client())
        try:
            network_api = client.resources.get(
                api_version="google.dev/v1",
                kind=serviceKind,
            )
            result = network_api.create(crd_manifest)
            return "new service request successful"
        except kubernetes.client.rest.ApiException as e: 
            logger.info(e.status)
            logger.debug(e)
            if e.status == 409:
                return f"service {serviceName} already exists"
            else:
                logger.info(e)

######################################################################
# Delete an existing connectivity service
######################################################################
@tool
def deleteService(
    name: Annotated[str, "The name of the service instance to delete"], 
    kind: Annotated[str, "The kubernetes kind of the service instance to delete"]
     ) -> str:
    """
    Delete a running network connectivity service instance.
    Only call this tool if explicitely stated in the network agent query or question.
    Always ask for an explicit confirmation by yes or no before deleting the service.
    """
    if name is None or kind is None:
        return {}, 400
    
    if GITOPS:
        filename = name+".yaml"
        result = delete_git_file(filename, f"{name} deletion")
        if result:
            return f"service {name} successfully submitted for deletion"
        else:
            return f"service {name} could not be deleted"

    else:
        client = kubernetes.dynamic.DynamicClient(get_client())
        try:
            network_api = client.resources.get(
                api_version="google.dev/v1", 
                kind=kind,
            )
            network_api.delete(name=name, namespace="automation")
            return f"Service {name} deleted request submitted"

        except kubernetes.client.rest.ApiException as e: 
            logger.info(e.status)
            logger.debug(e)
            if e.status == 404:
                logger.info("No service found")
                return "No service found"
            else:
                logger.info(e)


######################################################################
# Create a new connectivity test
######################################################################
class TestInput(BaseModel):
    name: Optional[str] = Field(None, description='a name for the test')
    virtualmachines: Optional[List[str]] = Field(
        None, description='Two IT application instance names to deploy the test to',
        min_length=2, 
        max_length=2
    )

@tool("create-test-tool", args_schema=TestInput, return_direct=True)
def createTest(payload)->Annotated[str, "Result of the create test request returned in Markdown"]:
    """
    Create a network connectivity test between two valid IT applications. 
    The IT application names must exist. 
    """
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

        return "Test created"

    except kubernetes.client.rest.ApiException as e: 
        logger.debug(e)
        if e.status == 409:
            logger.info("Already exists - skipping")
            return "Test already exists"
        else:
            logger.info(e)
            return str(e)

######################################################################
# Delete a connectivity test
######################################################################
@tool
def deleteTest(
    name: Annotated[str, "The name of the test instance to delete"]
    )->Annotated[str, "The result of the delete test request, return in Markdown format"]:
    """
    Delete a running network connectivity test, the name provided must be the name of a running test instance
    """

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:

        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="ConnectivityTest",
        )
        network_api.delete(name=name, namespace="automation")
        return f"Test {name} deleted"

    except kubernetes.client.rest.ApiException as e: 
        logger.debug(e)
        if e.status == 404:
            logger.info("No service found")
            return f"Test {name} not found"
        else:
            logger.info(e)
            return str(e)

######################################################################
# Get Service Performance Metrics
######################################################################
@tool
def getServicePerformanceMetrics(name, period):
    """
    Get service metrics
    """
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
