import jinja2
from jinja2 import Environment, FileSystemLoader
import os

async def get_k8s_manifest(file):
    environment = Environment(loader=FileSystemLoader("templates/"))
    template = environment.get_template(file)
    output=template.render(
        GOOGLE_REGION=os.getenv("GOOGLE_REGION"),
        GOOGLE_ZONE=os.getenv("GOOGLE_ZONE"),
        GOOGLE_PROJECT=os.getenv("GOOGLE_PROJECT"),
        )
    return output