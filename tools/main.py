import asyncio
import logging
from aiohttp import web
from aiohttp_swagger import *
import aiohttp_cors
from kubernetes import dynamic
import os
from kubernetes.dynamic.exceptions import ResourceNotFoundError
from utils.login import *
import utils.constants as constants
from utils.args import *

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

async def getCustomerLocations(request):
    """
    ---
    description: Retrieve all customer VPC locations
    tags:
    - Locations
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

    client = dynamic.DynamicClient(
        constants.api_client
    )

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
    except ResourceNotFoundError:
        return web.json_response({"result": []})

async def getServices(request):
    """
    Query a customers connectivity services
    Args:
        - Customer name: 
    Returns:
        Service descriptions
    """
    
    logger.info("Getting Service for customer %s", )

    return web.json_response({"result": "ok"})


async def createService():
    """
    Create a customer connectivity services
    Args:
        - Customer name: 
        - list of VPC locations to connect
        - list of firewall rules
    Returns:
        Service description
    """
    logger.info("Create a new Service")

    return web.json_response({"result": "ok"})
    

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
    # collect arguments
    parseargs()

    # login to k8s cluster
    login()

    # add rest endpoints
    addRoutes()

    # start the server   
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init())
    loop.run_forever()