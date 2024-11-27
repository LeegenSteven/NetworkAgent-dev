import logging
from jinja2 import Environment, FileSystemLoader
import os

logger = logging.getLogger(__name__)


##########################################################
# template the amf manifests
##########################################################
async def template_amf_manifest(folder, filename,address):
    environment = Environment(loader=FileSystemLoader(folder))
    template = environment.get_template(filename)
    output=template.render(
        GOOGLE_REGION=os.getenv("GOOGLE_REGION"),
        GOOGLE_ZONE=os.getenv("GOOGLE_ZONE"),
        GOOGLE_PROJECT=os.getenv("GOOGLE_PROJECT"),
        AMFADDRESS=address
        )
    return output