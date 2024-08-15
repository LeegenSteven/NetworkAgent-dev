import kopf
import logging
import ansible_runner
import utils.constants as constants
from services.sitetosite.lifecycle_tasks import *
from resources.wireguard.lifecycle_tasks import get_address_info

logger = logging.getLogger(__name__)

async def getExternalAddress(name):
    address_name=None
    result = await get_compute(name)
    interfaces = result.spec.get('networkInterface')
    for int in interfaces:
        accessConfig = int.get('accessConfig')
        if accessConfig is not None:
            address_name =int['accessConfig'][0]['natIpRef']['name']
            break

    address_info = await get_address_info(address_name)
    return address_info.spec['address']

async def getMgmtAddress(name):
    address=None
    result = await get_compute(name)
    interfaces = result.spec.get('networkInterface')
    for int in interfaces:
        if 'mgmt' not in int['networkRef']['external']:
            address =int['networkIpRef']['external']
            break

    return address

@kopf.on.create('connectivitytest')
async def createtest(spec, name, namespace, logger, **kwargs):
    logger.info("Create test case")
    
    # build the a and b end variables
    client=spec.get('virtualmachines')[0]
    server=spec.get('virtualmachines')[1]

    # check that vms exist
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    network_api = client.resources.get(
        api_version="compute.cnrm.cloud.google.com/v1beta1", 
        kind="ComputeInstance",
    )
    try:
        network_api.get(namespace="automation", name=client)
        network_api.get(namespace="automation", name=server)
    except:
        raise kopf.PermanentError("virtual machines not found")

    client_external_ip=await getExternalAddress(client)
    server_external_ip=await getExternalAddress(server)
    client_mgmt_ip=await getMgmtAddress(client)
    server_mgmt_ip=await getMgmtAddress(server)

    serverresult = await get_compute(server)
    # check the VM has a network and ip address, if not backoff until it does
    interfaces = serverresult.spec.get('networkInterface')
    for int in interfaces:
        accessConfig = int.get('accessConfig')
        if accessConfig is not None:
            server_address =int['accessConfig'][0]['natIpRef']['name']
            break

    server_address_info = await get_address_info(server_address)
    server_ip = server_address_info.spec['address']

    hosts = {
        'hosts': {
            'client': {
                'ansible_host': client_external_ip,
                'mgmt_ip': client_mgmt_ip,
                'ansible_user': 'admin_briannaughton_altostrat_co',
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': 'google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            },
            'server': {
                'ansible_host': server_external_ip,
                'mgmt_ip': server_mgmt_ip,
                'ansible_user': 'admin_briannaughton_altostrat_co',
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': 'google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            }
        }
    }
    logger.info(json.dumps(hosts, indent=4))
    r = ansible_runner.run(private_data_dir=constants.basedir+"/tests/playbooks", 
                           inventory={'all': hosts},
                           playbook='run.yaml')

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)

@kopf.on.delete('connectivitytest')
async def deletetest(spec, name, namespace, logger, **kwargs):
    logger.info("Delete test case")
    
    # build the a and b end variables
    client=spec.get('virtualmachines')[0]
    server=spec.get('virtualmachines')[1]

    client_external_ip=await getExternalAddress(client)
    server_external_ip=await getExternalAddress(server)
    client_mgmt_ip=await getMgmtAddress(client)
    server_mgmt_ip=await getMgmtAddress(server)

    serverresult = await get_compute(server)
    # check the VM has a network and ip address, if not backoff until it does
    interfaces = serverresult.spec.get('networkInterface')
    for int in interfaces:
        accessConfig = int.get('accessConfig')
        if accessConfig is not None:
            server_address =int['accessConfig'][0]['natIpRef']['name']
            break

    server_address_info = await get_address_info(server_address)
    server_ip = server_address_info.spec['address']

    hosts = {
        'hosts': {
            'client': {
                'ansible_host': client_external_ip,
                'mgmt_ip': client_mgmt_ip,
                'ansible_user': 'admin_briannaughton_altostrat_co',
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir+'/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            },
            'server': {
                'ansible_host': server_external_ip,
                'mgmt_ip': server_mgmt_ip,
                'ansible_user': 'admin_briannaughton_altostrat_co',
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': '/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            }
        }
    }
    logger.info(json.dumps(hosts, indent=4))
    r = ansible_runner.run(private_data_dir=constants.basedir+"/tests/playbooks", 
                           inventory={'all': hosts},
                           playbook='stop.yaml')

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
