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
import json
import utils.git_helpers as git
import yaml
import utils.globals as globals
from mcp.types import ToolAnnotations


logger = logging.getLogger(__name__)

# Custom YAML representer to preserve quotes for numeric strings
def represent_str(dumper, data):
    """
    Custom string representer that preserves quotes for numeric strings
    that should remain as strings (e.g., phone numbers, IDs starting with 0)
    """
    # Preserve quotes for numeric strings that start with 0 or other patterns
    # that should remain as strings
    if isinstance(data, str) and data.isdigit() and data.startswith('0'):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)

# Register the custom representer
yaml.add_representer(str, represent_str)

# if GITOPS true then the service deletion / creation
# is performed through the Gitea repository + Config Sync
# Otherwise it is executed directly through K8s apply/delete
GITOPS = True


######################################################################
# Resource to provide the network design doc
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getNetworkDesign()-> str:
    """
    Fetch the current network design document

    Returns:
        network design document in markdown format
    """
    logger.info("Getting network design from git")

    filename = "5gnetwork.md"
    result = git.get_git_file(git.DESIGN_REPO, filename)
    if result is not None:
        return result
    else:
        logger.error(f"{filename} could not be found")
        return None

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
            "namespace": "network",
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
            "namespace": "network",
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
            "namespace": "network",
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
        result = git.commit_git_file(filename,
                                 f"Deployment of {name} network location",
                                 gitops_manifest)
        if result:
            logger.info(f"service {filename} manifest successfully submitted for deployment")
        else:
            logger.error(f"service {filename} manifest error deploying:\n```yaml\n{gitops_manifest}\n```")
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
):
    """
    Delete an existing network location.

    Returns:
        result of the request
    """
    logger.info("Deleting VPC %s", name)

    if GITOPS:
        filename = name+"-network-location.yaml"
        result = git.delete_git_file(filename, f"{name} location deletion")
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
            network_api.delete(namespace="network", name=name)

            network_api = client.resources.get(
                api_version="compute.cnrm.cloud.google.com/v1beta1",
                kind="ComputeSubnetwork",
            )
            network_api.delete(namespace="network", name=name)

            network_api = client.resources.get(
                api_version="compute.cnrm.cloud.google.com/v1beta1",
                kind="ComputeFirewall",
            )
            network_api.delete(namespace="network", name=name)

        except kubernetes.client.rest.ApiException as e: 
            logger.info(e.status)
            logger.debug(e)
            if e.status == 409:
                return f"network location {name} already exists"
            else:
                logger.info(e)

    return "network location deleted successfully"

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
        Network service instances are returned as a list of kubernetes custom resource instance objects. 
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
# Get existing service instance by Name
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getServiceByName(name: str, kind: str)-> str:
    """
    Find a Network Service instance with the provided name and kind.

    Input Parameters: 
        name: the name of the network service instance   
        kind: the kubernetes kind associated with the network service instance. 

    Returns:
        Network service instance kubernetes custom resource instance object. 
        Providing general information, the network service configuration in the 'spec' section and 
        its current operational state in the 'status' section
    """
    logger.info(f"finding service instance {name} of kind {kind}")

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        # First, get the CRD to determine the correct API version
        crd_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        
        # Find the CRD for this kind that has the networkservices label
        crds = crd_api.get(label_selector="type=networkservices")
        target_crd = None
        
        for crd in crds.items:
            if crd['spec']['names']['kind'] == kind:
                target_crd = crd
                break
        
        if target_crd is None:
            return f"No CRD found for kind {kind} with label type=networkservices"
        
        # Get the API version from the CRD
        api_version = target_crd['spec']['group'] + '/' + target_crd['spec']['versions'][0]['name']
        
        # Now get the specific service instance
        svc_api = client.resources.get(
            kind=kind,
            api_version=api_version
        )

        # Get the specific service by name
        try:
            service = svc_api.get(name=name, namespace="network")
            service_dict = service.to_dict()
            text_representation = json.dumps(service_dict, indent=2)
            logger.debug(f"Found service: {text_representation}")
            return text_representation
        except kubernetes.client.rest.ApiException as get_e:
            if get_e.status == 404:
                return f"Service {name} of kind {kind} not found"
            else:
                raise get_e

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return f"No services found for kind {kind}"
        else:
            logger.error(f"Error retrieving service: {e}")
            return f"Error retrieving service: {e}"

######################################################################
# Create a new network service
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def createService(
    kind: Annotated[str, "the kubernetes kind for this network service instance"], 
    name: Annotated[str, "the kubernetes name for this network service instance"], 
    spec: Annotated[Dict, "the kubernetes spec object for the new network service"]
    ) -> str:
    """
    Tool used to deploy (also called instantiate) a new network service. 
    The types and specifications of network services available can be discovered by calling the getServiceDefinitions tool.
    Only call this tool if explicitely stated in the network agent query or question.
    """
    logger.info("Create a new Service %s", str(spec))

    # If the user gave the entire spec block then only keep its
    # content which should be the 'interfaces' description
    if "spec" in spec.keys():
        spec = spec["spec"]
    if not name:
        name = kind.lower()+str(uuid.uuid4())[:8]

    crd_manifest= { 
        "apiVersion": "google.dev/v1",
        "kind": kind,
        "metadata": {
            "name": name,
            "labels": {
                "graph": "true", 
                "monitor": "true"
            },
        },
        "spec": spec
    }

    crd_manifest_yaml = yaml.dump(crd_manifest, indent=2, allow_unicode=True, default_flow_style=False)
    logger.info(crd_manifest)
    if GITOPS:
        filename = f"{kind.lower()}-{name}.yaml"
        result = git.commit_git_file(filename,
                                 f"Deployment of {kind} {name}",
                                 crd_manifest_yaml)
        if result:
            return f"service {name} successfully submitted for deployment"
        else:
            return f"service {name} could not be deployed:\n```yaml\n{crd_manifest_yaml}\n```"

    else:
        client = kubernetes.dynamic.DynamicClient(get_client())
        try:
            network_api = client.resources.get(
                api_version="google.dev/v1",
                kind=kind,
            )
            result = network_api.create(crd_manifest)
            return "new service request successful"
        except kubernetes.client.rest.ApiException as e: 
            logger.info(e.status)
            logger.debug(e)
            if e.status == 409:
                return f"service {name} already exists"
            else:
                logger.info(e)

######################################################################
# Patch the status of the service
######################################################################
@globals.networkagent_mcp.tool()
def reinstallFailedService(
    kind: Annotated[str, "The kubernetes kind of the network service instance to mark as failed"],
    name: Annotated[str, "The kubernetes name of the running/deployed network service to mark as failed"]
    )->str:
    """
    Useful to reinstall and reconfigure network services that are in a failed state. 
    
    Patch a running network service instance status to Failed. When a network service instance status is updated 
    to 'Failed' from 'Running' it triggers a reinstallation of the network service software. 
    
    Returns:
        success or failure
    """
    logger.info(f"Patch service {name} of kind {kind} to 'Failed'")

    if name is None or kind is None:
        return {}, 400

    # validate the kind
    client = kubernetes.dynamic.DynamicClient(get_client())
    
    try:
        # First, get the CRD to determine the correct API version and validate the kind
        crd_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        
        # Find the CRD for this kind that has the networkservices label
        crds = crd_api.get(label_selector="type=networkservices")
        target_crd = None
        correct_kind = None
        
        # Perform case-insensitive matching
        for crd in crds.items:
            if crd['spec']['names']['kind'].lower() == kind.lower():
                target_crd = crd
                correct_kind = crd['spec']['names']['kind']  # Store the correctly-cased kind
                break
        
        if target_crd is None:
            # Get list of valid kinds for error message
            valid_kinds = [crd['spec']['names']['kind'] for crd in crds.items]
            return f"Invalid kind '{kind}'. Valid network service kinds are: {', '.join(valid_kinds)}"
        
        # Get the API version from the CRD
        api_version = target_crd['spec']['group'] + '/' + target_crd['spec']['versions'][0]['name']
        
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return "No network service definitions found"
        else:
            logger.error(f"Error validating kind: {e}")
            return f"Error validating kind: {e}"

    try:
        import datetime

        # need to update the status.{kind}.status to 'Failed' for the kubernetes kind/name given
        network_api = client.resources.get(
            api_version=api_version, 
            kind=correct_kind,
        )
        resource = network_api.get(name=name, namespace="network")
        resource_dict = resource.to_dict()

        resource_dict['status'][correct_kind.lower()]['status'] = 'Failed'

        # Patch the resource
        network_api.patch(
            body=resource_dict, 
            name=name, 
            namespace='network', 
            content_type='application/merge-patch+json'
        )

        return f"Service {name} marked as failed"

    except kubernetes.client.rest.ApiException as e: 
        logger.info(e.status)
        logger.debug(e)
        if e.status == 404:
            logger.info("No service found")
            return "No service found"
        else:
            logger.info(e)

######################################################################
# Delete an existing network service
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def deleteService(
    kind: Annotated[str, "The kubernetes kind of the network service instance to delete"],
    name: Annotated[str, "The kubenetes name of the running/deployed network service to delete"]
     ) -> str:
    """
    Delete a running network service instance.
    """
    if name is None or kind is None:
        return {}, 400
    
    if GITOPS:
        filename = f"{kind.lower()}-{name}.yaml"
        result = git.delete_git_file(filename, f"{name} deletion")
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
