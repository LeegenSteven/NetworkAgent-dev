# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Annotated, Dict
import logging
import kubernetes
from utils.k8s import get_client
import os
import uuid
from utils.git_helpers import *
import yaml
import utils.globals as globals
from mcp.types import ToolAnnotations


logger = logging.getLogger(__name__)

# if GITOPS true then the service deletion / creation
# is performed through the Gitea repository + Config Sync
# Otherwise it is executed directly through K8s apply/delete
GITOPS = True

######################################################################
# Get existing network locations tool
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getLocations()-> str:
    """
    Fetch a list of network locations that can be used to connect or interface with
    available network services

    Returns:
      A list of network locations in Markdown format.
    """
    logger.info("Getting locations" )

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        network_api = client.resources.get(
            api_version="compute.cnrm.cloud.google.com/v1beta1", 
            kind="ComputeSubnetwork",
        )
        result=network_api.get()
        locations=""

        for item in result.items:
            logger.debug(item)
            location = f"""
__Network Name:__ {item['metadata']['name']}
* _Description_: {item['spec']['description']}
* _CIDR_: {item['spec']['ipCidrRange']}
            """
            locations = locations + location

        return locations
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return "No locations found"
        else:
            logger.debug(e)


######################################################################
# Create a new GCP location
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def createLocation(
    name: Annotated[str, "the name of the new GCP VPC network, this must be unique amonst all other locations"],
    cidr: Annotated[str, "the IP Range for the location subnetwork, this must be unique amongst all the other location and must be a valid CIDR IP range."]
):
    """
    Create a new GCP VPC network location.

    Returns:
        result of the request
    """
    logger.info("Creating new VPC %s %s", name, cidr)

    # build up the network manifest in one file
    gitops_manifest=None

    network_crd_manifest={
        "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
        "kind": "ComputeNetwork",
        "metadata": {
            "name": name,
            "labels": {
                "graph": "true"
            },
        },
        "spec": {
            "routingMode": "REGIONAL",
            "autoCreateSubnetworks": False
        }
    }

    if GITOPS:
        gitops_manifest = yaml.dump(network_crd_manifest, indent=2)
    else:
        client = kubernetes.dynamic.DynamicClient(get_client())
        try:
            network_api = client.resources.get(
                api_version="compute.cnrm.cloud.google.com/v1beta1",
                kind="ComputeNetwork",
            )
            result = network_api.create(network_crd_manifest)
        except kubernetes.client.rest.ApiException as e: 
            logger.info(e.status)
            logger.debug(e)
            if e.status == 409:
                return f"network location {name} already exists"
            else:
                logger.info(e)

    subnet_crd_manifest= { 
        "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
        "kind": "ComputeSubnetwork",
        "metadata": {
            "name": name,
            "labels": {
                "graph": "true"
            }
        },
        "spec": {
            "ipCidrRange": cidr,
            "region": os.getenv("GOOGLE_REGION"),
            "description": f"{name} VPN Sub Network",
            "networkRef":{
            "name": name
            }
        }
    }

    if GITOPS:
        gitops_manifest=gitops_manifest+"\n---\n"+yaml.dump(subnet_crd_manifest, indent=2)
    else:
        client = kubernetes.dynamic.DynamicClient(get_client())
        try:
            network_api = client.resources.get(
                api_version="compute.cnrm.cloud.google.com/v1beta1",
                kind="ComputeSubnetwork",
            )
            result = network_api.create(subnet_crd_manifest)
        except kubernetes.client.rest.ApiException as e: 
            logger.info(e.status)
            logger.debug(e)
            if e.status == 409:
                return f"network subnet {name} already exists"
            else:
                logger.info(e)

    firewall_crd_manifest= { 
        "apiVersion": "compute.cnrm.cloud.google.com/v1beta1",
        "kind": "ComputeFirewall",
        "metadata": {
            "name": name,
            "labels": {
                "graph": "true"
            }
        },
        "spec":{
            "allow": [
                {
                    "protocol": "all",
                },
            ],
            "networkRef": {
                "name": name,
            },
            "priority": 500,
        }
    }

    if GITOPS:
        gitops_manifest=gitops_manifest+"\n---\n"+yaml.dump(firewall_crd_manifest, indent=2)

        filename = name+"-network-location.yaml"
        result = commit_git_file(filename,
                                 f"Deployment of {name} network location",
                                 gitops_manifest)
        if result:
            logger.info(f"service {filename} manifest successfully submitted for deployment")
        else:
            logger.error(f"service {filename} manifest errpr deploying:\n```yaml\n{gitops_manifest}\n```")
    else:
        client = kubernetes.dynamic.DynamicClient(get_client())
        try:
            network_api = client.resources.get(
                api_version="compute.cnrm.cloud.google.com/v1beta1",
                kind="ComputeFirewall",
            )
            result = network_api.create(firewall_crd_manifest)
        except kubernetes.client.rest.ApiException as e: 
            logger.info(e.status)
            logger.debug(e)
            if e.status == 409:
                return f"network firewall {name} already exists"
            else:
                logger.info(e)

    return "network created successfully"


######################################################################
# Create a new GCP location
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def deleteLocation(
    name: Annotated[str, "the name of the network location to delete"],
    namespace: Annotated[str, "the namespace of the network to delete - usually the same as the name"],
):
    """
    Delete an existing network location.

    Returns:
        result of the request
    """
    logger.info("Deleting VPC %s", name)

    if GITOPS:
        filename = name+"-firewall.yaml"
        result = delete_git_file(filename, f"{name} firewall deletion")
        if result:
            logger.error (f"service {filename} successfully submitted for deletion")
        else:
            logger.error(f"service {filename} could not be deleted")

        filename = name+"-subnet.yaml"
        result = delete_git_file(filename, f"{name} subnetwork deletion")
        if result:
            logger.error (f"service {filename} successfully submitted for deletion")
        else:
            logger.error(f"service {filename} could not be deleted")

        filename = name+"-network.yaml"
        result = delete_git_file(filename, f"{name} network deletion")
        if result:
            logger.error (f"service {filename} successfully submitted for deletion")
        else:
            logger.error(f"service {filename} could not be deleted")

    else:
        client = kubernetes.dynamic.DynamicClient(get_client())
        try:
            network_api = client.resources.get(
                api_version="compute.cnrm.cloud.google.com/v1beta1",
                kind="ComputeNetwork",
            )
            network_api.delete(namespace=namespace, name=name)

            network_api = client.resources.get(
                api_version="compute.cnrm.cloud.google.com/v1beta1",
                kind="ComputeSubnetwork",
            )
            network_api.delete(namespace=namespace, name=name)

            network_api = client.resources.get(
                api_version="compute.cnrm.cloud.google.com/v1beta1",
                kind="ComputeFirewall",
            )
            network_api.delete(namespace=namespace, name=name)

        except kubernetes.client.rest.ApiException as e: 
            logger.info(e.status)
            logger.debug(e)
            if e.status == 409:
                return f"network location {name} already exists"
            else:
                logger.info(e)

    return "network deleted successfully"

######################################################################
# Get a list of Service Definitions
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getServiceDefinitions()->str:
    """
    Fetch the available network services that can be instantiated.

    Returns:
        A set of kubernetes custom resource CRDs that can orchestrate network services. 
        Each CRD provides the following information:
        - description of the connectivity service functionality
        - a spec section that has the name of the 'kind' for each connectivity service and an OpenAPI schema describing the information required to instantiate the kind connectivity service.
    """
    logger.info("Getting Network Service definitions")

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        items=network_api.get(label_selector="type=networkservices")
        logger.debug("NW SERVICES ITEMS")
        logger.debug(items)
        services=[]
        for item in items.items:
            services.append(f"```json\n{item}\n```")

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
# Get existing services
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getServices()-> str:
    """
    Fetch the network service instances that are currently deployed.

    Returns:
        Network service instances are returned as a list of kubernetes custom resourcec instance objects. 
        Providing general information, the network service configuration in the 'spec' section and 
        its current operational state in the 'status' section
    """
    logger.info("finding service instances")

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        services_list=""
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        service_descriptors=network_api.get(label_selector="type=networkservices")
        for crd in service_descriptors.items:
            svc_api = client.resources.get(
                kind=crd['spec']['names']['kind'],
                api_version=crd['spec']['group']+'/'+crd['spec']['versions'][0]['name'],
            )

            services=svc_api.get()
            for item in services.items:
                logger.debug(item)
                item_dict = item.to_dict()
                text_representation = json.dumps(item_dict, indent=2)
                services_list=services_list+text_representation

        logger.debug(services_list)
        return services_list

    except  kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return """No services found"""
        else:
            logger.info(e)

######################################################################
# Create a new network service
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def createService(
    serviceKind: Annotated[str, "the kubernetes kind for this network service instance"], 
    serviceName: Annotated[str, "the kubernetes name for this network service instance"], 
    serviceSpec: Annotated[Dict, "the kubernetes spec object for the new network service"]
    ) -> str:
    """
    Tool used to deploy (also called instantiate) a new network service. 
    The types and specifications of network services available can be discovered by calling the getServiceDefinitions tool.
    Only call this tool if explicitely stated in the network agent query or question.
    """
    logger.info("Create a new Service %s", str(serviceSpec))

    # If the user gave the entire spec block then only keep its
    # content which should be the 'interfaces' description
    if "spec" in serviceSpec.keys():
        serviceSpec = serviceSpec["spec"]
    if not serviceName:
        serviceName = serviceKind.lower()+str(uuid.uuid4())[:8]

    crd_manifest= { 
        "apiVersion": "google.dev/v1",
        "kind": serviceKind,
        "metadata": {
            "name": serviceName,
            "labels": {
                "graph": "true", 
                "monitor": "true"
            },
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
            return f"service {serviceName} successfully submitted for deployment"
        else:
            return f"service {serviceName} could not be deployed:\n```yaml\n{crd_manifest_yaml}\n```"

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
# Delete an existing network service
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def deleteService(
    name: Annotated[str, "The name of the running/deployed network service to delete"], 
    kind: Annotated[str, "The kubernetes kind of the network service instance to delete"]
     ) -> str:
    """
    Delete a running network service instance.
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
            network_api.delete(name=name, namespace="network")
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
# Get existing tests
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getRunningTests()-> str:
    """
    Fetch the test instances that are currently running.

    Returns:
        A list of tests and their status in Markdown format
    """
    logger.info("get running tests")

    client = kubernetes.dynamic.DynamicClient(get_client())
    try:
        test_list=""
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="UETest",
        )
        tests=network_api.get()
        for t in tests.items:
            logger.debug(t)
            test_description=f"""
* __Test__: {t['metadata']['name']}
"""
            test_list=test_list+test_description
        logger.debug(test_list)
        return test_list

    except  kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return """No tests found"""
        else:
            logger.info(e)

######################################################################
# Run new test
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def runTest(
    name: Annotated[str, "The name of the test to create"], 
    ueRanSimName: Annotated[str, "The name of the UERanSim network service to deploy the test"], 
    dnnName: Annotated[str, "The name of the DataNetwork network service to send traffic to"], 
    )-> str:
    """
    Tool used to deploy (also called instantiate) a new test.
    Only call this tool if explicitely stated in the network agent query or question.
    Always ask for an explicit confirmation by yes or no before creating the service.
    """
    logger.info("run a new test %s %s %s", name, ueRanSimName, dnnName)

    client = kubernetes.dynamic.DynamicClient(get_client())
    try:
        test_list=""
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="UETest",
        )

        test_manifest= { 
            "apiVersion": "google.dev/v1",
            "kind": "UETest",
            "metadata": {
                "name": name,
                "namespace": "network",
            },
            "spec": {
                "ueransim": {
                    "name": ueRanSimName,
                    "namespace": "network"
                },
                "datanetwork": {
                    "name": dnnName,
                    "namespace": "network"
                },
            }
        }

        network_api.create(test_manifest)
        return "Test started"

    except  kubernetes.client.rest.ApiException as e:
        logger.info(e.status)
        if e.status == 409:
            return f"service {name} already exists"
        else:
            logger.error(e)

######################################################################
# Delete running test
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def deleteTest(
    name: Annotated[str, "The name of the test to delete"], 
    )-> str:
    """
    Delete a running test.
    Only call this tool if explicitely stated in the network agent query or question.
    """
    logger.info("delete test %s", name)

    client = kubernetes.dynamic.DynamicClient(get_client())
    try:
        test_list=""
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="UETest",
        )
        network_api.delete(name=name,namespace="network")
        return "Test deleted"

    except  kubernetes.client.rest.ApiException as e:
        logger.error(e)
