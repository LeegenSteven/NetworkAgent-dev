import asyncio
import logging
from aiohttp import web
from aiohttp_swagger import *
import aiohttp_cors
import kubernetes
import os
import json

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
    produces:
    - text/json
    responses:
        "200":
            description: successful operation. Return json object with VPC location informastion
        "405":
            description: invalid HTTP Method
    """
    logger.info("Getting locations for customer %s", )

    if 'name' not in request.match_info:
        return web.json_response({"errro": "name is required"})

    # get the customer name
    name = request.match_info['name']

    logger.info("finding networks for %s", name)

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())

    try:
        network_api = client.resources.get(
            api_version="compute.cnrm.cloud.google.com/v1beta1", 
            kind="ComputeSubnetwork",
        )
        items=network_api.get(label_selector=f"customer={name}")
        locations=[]
        for item in items.items:
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
# Get existing connectivity services
######################################################################
async def getServices(request):
    """
    Query a customers connectivity services
    Args:
        - Customer name: 
    Returns:
        Service descriptions
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
        locations=[]
        for item in items.items:
            logger.info(item)
            location = {
                'name': item['metadata']['name'],
            }
            locations.append(location)

        return web.json_response(locations)
    except kubernetes.ResourceNotFoundError:
        return web.json_response({"result": []})

    return web.json_response({"result": "ok"})

######################################################################
# Create a new connectivity service
######################################################################
async def createService(request):
    """
    Create a customer connectivity services
    Args:
        - Customer name: 
        - 2 VPC locations to connect
        - list of firewall rules (later)
    Returns:
        Service description
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


if __name__ == "__main__":
    # add rest endpoints
    addRoutes()

    # start the server   
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init())
    loop.run_forever()