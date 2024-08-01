import asyncio
import logging
from aiohttp import web
import aiohttp_cors
from kubernetes import client
from utils.login import *
import utils.constants as constants

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

async def getServices(request):
    logger.info("Getting Service")


    # # get the resource and print out data
    # resource = constants.custom_api_instance.get_namespaced_custom_object(
    #     group="compute.cnrm.cloud.google.com",
    #     version="v1beta1",
    #     name="site1-dev",
    #     namespace="automation",
    #     plural="computernetworks",
    # )

    # Enumerate e.g. Pods
    resp = constants.v1_api_instance.list_pod_for_all_namespaces()
    for i in resp.items:
        print(f"{i.status.pod_ip}\t{i.metadata.namespace}\t{i.metadata.name}")

    return web.json_response({"result": "ok"})


async def createService():
    logger.info("Create a new Service")

    return web.json_response({"result": "ok"})
    

######################################################################
# Start the server and load routes
######################################################################
async def init():
    logger.info('starting server on 0.0.0.0:8080')
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner,host='0.0.0.0', port=8080, ssl_context=None)
    await site.start()

def addRoutes():
    getServiceRoute=app.router.add_get("/services", getServices)
    cors.add(getServiceRoute, corsOptions)
    createServiceRoute=app.router.add_post("/service", createService)
    cors.add(createServiceRoute, corsOptions)

if __name__ == "__main__":
    login()

    # add rest endpoints
    addRoutes()

    # start the server   
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init())
    loop.run_forever()