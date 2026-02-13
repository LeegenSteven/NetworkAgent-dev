import logging
import os
from aiohttp import web
import aiohttp_cors

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.realpath(__file__))

# Initialize aiohttp application with no middleware
app = web.Application()

# Setup CORS for aiohttp routes
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
        allow_methods="*"
    )
})

async def train(request):
    logger.info("train")
    return web.json_response({'success': True})

if __name__ == "__main__":
    train_route = app.router.add_get('/train', train)    
    # Add CORS to API routes
    cors.add(train_route)

    logger.info("starting gnn agent...")
    web.run_app(app, port=8080)
