import asyncio
import logging
from aiohttp import web
from aiohttp_swagger import *
import aiohttp_cors
import kubernetes
import os
import json
from pathlib import Path

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)

app = web.Application()
corsOptions={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
        allow_methods="*"
    )
}
cors = aiohttp_cors.setup(app, defaults=corsOptions)

######################################################################
# Get existing customer locations
######################################################################
async def getCustomerLocations(request):
    """
    ---
    description: Retrieve all customer VPC locations
    parameters: 
    - in: path
      name: name
      description: Customer Name
      required: true
      schema:
          type: string
    produces:
    - text/json
    responses:
        "200":
            description: successful operation. Return json object with VPC location informastion
    """

    if 'name' not in request.match_info:
        return web.json_response({"errro": "name is required"})

    # get the customer name
    name = request.match_info['name']

    logger.info("Getting locations for customer %s", name )

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

        return web.json_response(locations)
    except kubernetes.ResourceNotFoundError:
        return web.json_response({"result": []})

######################################################################
# Get a list of Service Definitions
######################################################################
async def getServiceDefinitions(request):
    """
    ---
    description: Get Connectivity Service Definitions
    produces:
    - text/json
    responses:
        "200":
            description: successful operation. Return json object with Service Definition CRD descriptors
    """
    
    logger.info("Getting Service definitions")

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

        return web.json_response(services)
    except kubernetes.dynamic.exceptions.ResourceNotFoundError:
        return web.json_response([])

######################################################################
# Get existing connectivity services
######################################################################
async def getServices(request):
    """
    ---
    description: Get Services
    parameters: 
    - in: path
      name: name
      description: Customer Name
      required: true
      schema:
          type: string
    produces:
    - text/json
    responses:
        "200":
            description: successful operation. Return json object with Services
    """
    
    logger.info("Getting Service for customer %s", )

    if 'name' not in request.match_info:
        return web.json_response({"errro": "name is required"})

    # get the customer name
    name = request.match_info['name']

    logger.info("finding networks for %s", name)

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())

    try:
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="ConnectivityService",
        )
        items=network_api.get(label_selector=f"customer={name}")
        services=[]
        for item in items.items:
            logger.info(item)
            service = {
                'customer': item['metadata']['labels']['customer'],
                'name': item['metadata']['name'],
                'type': item['spec']['type'],
                'interfaces': item['spec']['interfaces'],
            }
            services.append(service)

        return web.json_response(services)
    except kubernetes.dynamic.exceptions.ResourceNotFoundError:
        return web.json_response([])



######################################################################
# Create a new connectivity service
######################################################################
async def createService(request):
    """
    ---
    description: Create a new connectivity service
    parameters:
    - in: body
        name: body
        schema:
            id: User
            required:
            - email
            - name
            properties:
            email:
                type: string
                description: email for user
            name:
                type: string
                description: name for user
    produces:
    - text/json
    responses:
        "201":
            description: successful operation. Return json object with new connectivity service informastion
    """
    logger.info("Create a new Service")
    params = await request.json()

    if (params['identifier'] is None) or (params['firstname'] is None) or (params['surname'] is None):
        return web.json_response(json.dumps({'error': 'firstname and lastname are required'}))

    name = params['name']
    site1 = params['site1']
    site2 = params['site2']

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())

    try:
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="ConnectivityService",
        )
        # items=network_api.create(

        # )

        return web.json_response({"result": []})
    except kubernetes.ResourceNotFoundError:
        return web.json_response({"result": []})

######################################################################
# Start the server and load routes
######################################################################
async def init():
    logger.info('starting server on 0.0.0.0:'+str(os.environ.get("PORT", 8080)))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner,host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), ssl_context=None)
    await site.start()

def addRoutes():
    getLocationsRoute=app.router.add_get("/locations/{name}", getCustomerLocations)
    cors.add(getLocationsRoute, corsOptions)

    getServiceRoute=app.router.add_get("/services/{name}", getServices)
    cors.add(getServiceRoute, corsOptions)

    createServiceRoute=app.router.add_post("/service", createService)
    cors.add(createServiceRoute, corsOptions)

    getServiceDefinitionsRoute=app.router.add_get("/definitions", getServiceDefinitions)
    cors.add(getServiceDefinitionsRoute, corsOptions)


if __name__ == "__main__":
    # add rest endpoints
    addRoutes()
    setup_swagger(app)

    # check if kubeconfig path exists
    if os.path.exists(Path.home()/".kube"):
        kubernetes.config.load_kube_config()
    else:
        kubernetes.config.load_incluster_config()

    # start the server   
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init())
    loop.run_forever()