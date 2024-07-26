import kopf
import logging
import ansible_runner
import kubernetes
from pathlib import Path
import os

@kopf.on.create('edgeappliances')
def create_fn(spec, name, namespace, logger, **kwargs):
    logging.info(f"A handler is called with spec: {spec}")

    edgevm = spec.get('edgevm')
    if not edgevm:
        raise kopf.PermanentError(f"edgevm must be set. Got {edgevm!r}.")

    cwd = os.getcwd()
    pdir = cwd+"/playbooks"
    logging.info("path = %s", pdir)

    logging.info("running playbook on edgevm = %s", edgevm)

    r = ansible_runner.run(private_data_dir=pdir, playbook='install.yaml')
    logging.info("status = %s", r.status)


