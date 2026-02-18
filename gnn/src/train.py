
import logging
import os
import threading
from aiohttp import web
import aiohttp_cors
from train.pipeline import run_training_pipeline

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
    logger.info("Received request to start training")
    # Run training in background thread/process
    # Threading is simplest for now.
    t = threading.Thread(target=run_training_pipeline)
    t.start()
    return web.json_response({'success': True, 'message': 'Training started in background.'}, status=202)

async def health(request):
    return web.json_response({'status': 'UP'})

if __name__ == "__main__":
    train_route = app.router.add_get('/train', train)    
    health_route = app.router.add_get('/health', health)
    
    # Add CORS to API routes
    cors.add(train_route)
    cors.add(health_route)

    logger.info("starting gnn training service...")
    port = int(os.environ.get('PORT', 8081))
    web.run_app(app, port=port)
