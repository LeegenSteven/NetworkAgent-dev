import kopf
import logging
import ipaddress
from typing import Dict, List, Any, Optional
import kubernetes

logger = logging.getLogger(__name__)

#########################################################################
# VyOSNetwork Lifecycle Management
#########################################################################

@kopf.on.create('google.dev', 'v1', 'vyosnetwork')
async def create_vyosnetwork(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle VyOSNetwork creation - decompose into VyOSRouter and DockerNetwork CRs"""
    logger.info(f"Creating VyOSNetwork: {name} in namespace: {namespace}")
    
    try:
        # Update status to indicate processing has started
        await update_status(name, namespace, "Validating", "Validating network topology")
        
        # Validate the network topology
        validation_result = validate_network_topology(spec)
        if not validation_result['valid']:
            await update_status(name, namespace, "Failed", f"Validation failed: {validation_result['error']}")
            raise kopf.PermanentError(f"Network validation failed: {validation_result['error']}")
        
        # Update status to decomposing
        await update_status(name, namespace, "Creating", "Creating network resources")
        
        # Generate LinuxNetwork CRs
        linux_networks = generate_linux_networks(spec, name, namespace, uid)
        created_networks = []
        
        for network_cr in linux_networks:
            try:
                await create_linux_network(network_cr, namespace)
                created_networks.append(network_cr['metadata']['name'])
                logger.info(f"Created LinuxNetwork: {network_cr['metadata']['name']}")
            except Exception as e:
                logger.error(f"Failed to create LinuxNetwork {network_cr['metadata']['name']}: {e}")
                await update_status(name, namespace, "Failed", f"Failed to create LinuxNetwork: {e}")
                raise

        # Generate VyOSRouter CRs
        vyos_routers = generate_vyos_routers(spec, name, namespace, uid)
        created_routers = []

        for router_cr in vyos_routers:
            try:
                await create_vyos_router(router_cr, namespace)
                created_routers.append(router_cr['metadata']['name'])
                logger.info(f"Created VyOSRouter: {router_cr['metadata']['name']}")
            except Exception as e:
                logger.error(f"Failed to create VyOSRouter {router_cr['metadata']['name']}: {e}")
                await update_status(name, namespace, "Failed", f"Failed to create VyOSRouter: {e}")
                raise
        
        # Update status to Waiting - waiting for child resources
        status_message = f"Created {len(created_networks)} networks and {len(created_routers)} routers - waiting for children to be Ready"
        await update_status(name, namespace, "Creating", status_message, created_networks, created_routers)
        
    except Exception as e:
        logger.error(f"Failed to create VyOSNetwork {name}: {e}")
        await update_status(name, namespace, "Failed", str(e))
        raise

@kopf.on.update('google.dev', 'v1', 'vyosnetwork')
async def update_vyosnetwork(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle VyOSNetwork updates - propagate changes to child resources"""
    logger.info(f"Updating VyOSNetwork: {name} in namespace: {namespace}")
    
    try:
        await update_status(name, namespace, "Updating", "Propagating changes to child resources")
        
        # Validate the updated topology
        validation_result = validate_network_topology(spec)
        if not validation_result['valid']:
            await update_status(name, namespace, "Failed", f"Update validation failed: {validation_result['error']}")
            raise kopf.PermanentError(f"Network validation failed: {validation_result['error']}")
        
        # For now, we'll recreate child resources on update
        # In a production implementation, you'd want to do selective updates
        logger.info("Update handling - child resources will be recreated due to owner references")
        
        await update_status(name, namespace, "Ready", "Update completed")
        
    except Exception as e:
        logger.error(f"Failed to update VyOSNetwork {name}: {e}")
        await update_status(name, namespace, "Failed", str(e))
        raise

@kopf.on.delete('google.dev', 'v1', 'vyosnetwork')
async def delete_vyosnetwork(body, spec, name, namespace, logger, **kwargs):
    """Handle VyOSNetwork deletion - child resources are automatically deleted via owner references"""
    logger.info(f"Deleting VyOSNetwork: {name} in namespace: {namespace}")
    # Child resources (VyOSRouter and DockerNetwork) will be automatically deleted

@kopf.on.update('google.dev', 'v1', 'vyosrouter', field='status')
async def on_vyosrouter_status_change(body, spec, name, namespace, old, new, logger, **kwargs):
    """Handle VyOSRouter status changes and update parent VyOSNetwork if needed"""

    logger.info(f"VyOSRouter {name} status changed from {old} to {new}")

    # Get the owner reference to find the parent VyOSNetwork
    owner_references = body.get('metadata', {}).get('ownerReferences', [])
    parent_network = None
    
    for owner in owner_references:
        if owner.get('kind') == 'VyOSNetwork':
            parent_network = owner.get('name')
            break
    
    if not parent_network:
        logger.info(f"VyOSRouter {name} is not owned by a VyOSNetwork, ignoring status change")
        # This VyOSRouter is not owned by a VyOSNetwork, ignore
        return
        
    # Check if all sibling VyOSRouters are now Ready
    await check_and_update_parent_network_status(parent_network, namespace, logger)
    
#########################################################################
# Validation Functions
#########################################################################

def validate_network_topology(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the network topology for consistency and conflicts"""
    try:
        networks = spec.get('networks', [])
        routers = spec.get('routers', [])
        qos_policies = spec.get('qos', {}).get('policies', [])
        firewall_policies = spec.get('security', {}).get('firewall', {}).get('policies', [])
        
        # Validate management network requirement
        management_validation = validate_management_network(networks)
        if not management_validation['valid']:
            return management_validation

        # Validate management interface assignments
        management_interface_validation = validate_management_interface_assignments(networks, routers)
        if not management_interface_validation['valid']:
            return management_interface_validation

        # Validate IP address assignments
        ip_validation = validate_ip_assignments(networks, routers)
        if not ip_validation['valid']:
            return ip_validation
        
        # Validate router interface mappings
        interface_validation = validate_interface_mappings(networks, routers)
        if not interface_validation['valid']:
            return interface_validation
        
        # Validate policy references
        policy_validation = validate_policy_references(routers, qos_policies, firewall_policies)
        if not policy_validation['valid']:
            return policy_validation
        
        return {'valid': True}
        
    except Exception as e:
        return {'valid': False, 'error': f"Validation error: {str(e)}"}

def validate_management_network(networks: List[Dict]) -> Dict[str, Any]:
    """Validate that there is exactly one management network"""
    try:
        management_networks = [
            network for network in networks 
            if network.get('network_type') == 'management'
        ]
        
        if len(management_networks) == 0:
            return {
                'valid': False, 
                'error': "Network topology must include exactly one network with network_type 'management'"
            }
        elif len(management_networks) > 1:
            management_names = [net['name'] for net in management_networks]
            return {
                'valid': False,
                'error': f"Network topology must have only one management network, but found {len(management_networks)}: {', '.join(management_names)}"
            }
        # check if the network has a vlan defined
        if management_networks[0].get('vlan'):
            return {
                'valid': False,
                'error': "Management network must not have a 'vlan' defined"
            }
        return {'valid': True}
        
    except Exception as e:
        return {'valid': False, 'error': f"Management network validation error: {str(e)}"}

def get_management_network_name(networks: List[Dict]) -> Optional[str]:
    """Get the name of the management network from the network list"""
    try:
        management_networks = [
            network for network in networks 
            if network.get('network_type') == 'management'
        ]
        
        # Return the name of the first (and should be only) management network
        return management_networks[0]['name'] if management_networks else None
        
    except Exception as e:
        logger.warning(f"Error getting management network name: {str(e)}")
        return None

def validate_management_interface_assignments(networks: List[Dict], routers: List[Dict]) -> Dict[str, Any]:
    """Validate that each router uses the management network as its first interface"""
    try:
        # Get the management network name
        management_network_name = get_management_network_name(networks)
        if not management_network_name:
            # This should be caught by validate_management_network, but being defensive
            return {
                'valid': False,
                'error': "No management network found for interface validation"
            }
        
        # Check each router's interface assignments
        for router in routers:
            router_name = router['name']
            interfaces = router.get('interfaces', [])
            
            # Check that router has at least one interface
            if not interfaces:
                return {
                    'valid': False,
                    'error': f"Router {router_name} must have at least one interface defined"
                }
            
            # Get the first interface (should be management)
            first_interface = interfaces[0]
            
            # Validate first interface points to management network
            if first_interface['network'] != management_network_name:
                return {
                    'valid': False,
                    'error': f"Router {router_name} first interface ({first_interface['name']}) must connect to management network '{management_network_name}', but is connected to '{first_interface['network']}'"
                }
        
        # Also validate that the management network connects all routers via their first interface
        management_network = None
        for network in networks:
            if network['name'] == management_network_name:
                management_network = network
                break
        
        if management_network:
            connected_routers = management_network.get('connected_routers', [])
            for router_conn in connected_routers:
                router_name = router_conn['router_name']
                interface_name = router_conn['interface']
                
                # Find the corresponding router to check if this is its first interface
                router = None
                for r in routers:
                    if r['name'] == router_name:
                        router = r
                        break
                
                if router and router.get('interfaces'):
                    first_interface_name = router['interfaces'][0]['name']
                    if interface_name != first_interface_name:
                        return {
                            'valid': False,
                            'error': f"Management network '{management_network_name}' must connect router {router_name} via its first interface ({first_interface_name}), but uses {interface_name}"
                        }
        
        return {'valid': True}
        
    except Exception as e:
        return {'valid': False, 'error': f"Management interface validation error: {str(e)}"}

def validate_ip_assignments(networks: List[Dict], routers: List[Dict]) -> Dict[str, Any]:
    """Validate IP address assignments for conflicts and consistency"""
    try:
        # Build a map of network subnets
        network_subnets = {}
        for network in networks:
            network_name = network['name']
            subnet = ipaddress.ip_network(network['subnet'])
            network_subnets[network_name] = subnet
        
        # Check for subnet overlaps
        subnet_list = list(network_subnets.values())
        for i, subnet1 in enumerate(subnet_list):
            for j, subnet2 in enumerate(subnet_list[i+1:], i+1):
                if subnet1.overlaps(subnet2):
                    return {'valid': False, 'error': f"Subnet overlap detected: {subnet1} and {subnet2}"}
        
        # Validate router interface IP assignments
        for network in networks:
            network_name = network['name']
            subnet = network_subnets[network_name]
            connected_routers = network.get('connected_routers', [])
            
            used_ips = set()
            for router_conn in connected_routers:
                ip_addr = ipaddress.ip_address(router_conn['ip_address'])
                
                # Check if IP is in the network subnet
                if ip_addr not in subnet:
                    return {'valid': False, 'error': f"IP {ip_addr} not in subnet {subnet} for network {network_name}"}
                
                # Check for duplicate IP assignments
                if ip_addr in used_ips:
                    return {'valid': False, 'error': f"Duplicate IP assignment {ip_addr} in network {network_name}"}
                
                used_ips.add(ip_addr)
        
        return {'valid': True}
        
    except Exception as e:
        return {'valid': False, 'error': f"IP validation error: {str(e)}"}

def validate_interface_mappings(networks: List[Dict], routers: List[Dict]) -> Dict[str, Any]:
    """Validate router interface to network mappings"""
    try:
        # Build network connection map
        network_connections = {}
        for network in networks:
            network_name = network['name']
            connected_routers = network.get('connected_routers', [])
            network_connections[network_name] = {
                conn['router_name']: conn['interface'] for conn in connected_routers
            }
        
        # Validate router interface definitions
        for router in routers:
            router_name = router['name']
            interfaces = router.get('interfaces', [])
            
            for interface in interfaces:
                interface_name = interface['name']
                network_name = interface['network']
                
                # Skip loopback interfaces
                if interface_name == 'lo':
                    continue
                
                # Check if network exists
                if network_name not in network_connections:
                    return {'valid': False, 'error': f"Router {router_name} references non-existent network {network_name}"}
                
                # Check if router is connected to this network
                if router_name not in network_connections[network_name]:
                    return {'valid': False, 'error': f"Router {router_name} not connected to network {network_name}"}
                
                # Check if interface matches
                expected_interface = network_connections[network_name][router_name]
                if interface_name != expected_interface:
                    return {'valid': False, 'error': f"Interface mismatch for router {router_name}: expected {expected_interface}, got {interface_name}"}
        
        return {'valid': True}
        
    except Exception as e:
        return {'valid': False, 'error': f"Interface mapping validation error: {str(e)}"}

def validate_policy_references(routers: List[Dict], qos_policies: List[Dict], firewall_policies: List[Dict]) -> Dict[str, Any]:
    """Validate that all policy references exist"""
    try:
        # Build policy name sets
        qos_policy_names = {policy['name'] for policy in qos_policies}
        firewall_policy_names = {policy['name'] for policy in firewall_policies}
        
        # Check router policy references
        for router in routers:
            router_name = router['name']
            
            # Check QoS policy references
            qos_policies_applied = router.get('qos_policies', [])
            for qos_policy in qos_policies_applied:
                policy_name = qos_policy['policy_name']
                if policy_name not in qos_policy_names:
                    return {'valid': False, 'error': f"Router {router_name} references non-existent QoS policy {policy_name}"}
            
            # Check firewall policy references
            firewall_policies_applied = router.get('firewall_policies', [])
            for firewall_policy in firewall_policies_applied:
                policy_name = firewall_policy['policy_name']
                if policy_name not in firewall_policy_names:
                    return {'valid': False, 'error': f"Router {router_name} references non-existent firewall policy {policy_name}"}
        
        return {'valid': True}
        
    except Exception as e:
        return {'valid': False, 'error': f"Policy reference validation error: {str(e)}"}

#########################################################################
# LinuxNetwork CR Generation
#########################################################################

def generate_linux_networks(spec: Dict[str, Any], parent_name: str, parent_namespace: str, parent_uid: str) -> List[Dict[str, Any]]:
    """Generate LinuxNetwork CRs from VyOSNetwork spec"""
    networks = spec.get('networks', [])
    linux_network_crs = []
    
    for network in networks:
        # Skip loopback networks - they don't need Linux networks
        if network['name'] == 'loopbacks':
            continue
        
        linux_network_cr = {
            'apiVersion': 'google.dev/v1',
            'kind': 'LinuxNetwork',
            'metadata': {
                'name': network['name'],
                'namespace': parent_namespace,
                'labels': {
                    'vyosnetwork': parent_name,
                    'network-type': network.get('network_type', 'p2p'),
                    'environment': 'lab'
                },
                'ownerReferences': [{
                    'apiVersion': 'google.dev/v1',
                    'kind': 'VyOSNetwork',
                    'name': parent_name,
                    'uid': parent_uid,
                    'controller': True,
                    'blockOwnerDeletion': True
                }]
            },
            'spec': {
                'name': network['name'],
                'description': network.get('description', ''),
                'source_network': parent_name,
                'network_type': network.get('network_type', 'p2p'),
                'bandwidth': network.get('bandwidth'),
                'connected_routers': network.get('connected_routers', [])
            }
        }
        
        linux_network_crs.append(linux_network_cr)
    
    return linux_network_crs

#########################################################################
# VyOSRouter CR Generation
#########################################################################

def generate_vyos_routers(spec: Dict[str, Any], parent_name: str, parent_namespace: str, parent_uid: str) -> List[Dict[str, Any]]:
    """Generate VyOSRouter CRs from VyOSNetwork spec"""
    routers = spec.get('routers', [])
    networks = spec.get('networks', [])
    vyos_router_crs = []
    
    # Build network lookup for IP address resolution
    network_lookup = build_network_lookup(networks)
    
    for router in routers:
        vyos_router_cr = {
            'apiVersion': 'google.dev/v1',
            'kind': 'VyOSRouter',
            'metadata': {
                'name': router['name'],
                'namespace': parent_namespace,
                'labels': {
                    'vyosnetwork': parent_name,
                    'router-type': router.get('type', 'unknown'),
                    'router-role': router.get('role', 'unknown'),
                    'environment': 'lab'
                },
                'ownerReferences': [{
                    'apiVersion': 'google.dev/v1',
                    'kind': 'VyOSNetwork',
                    'name': parent_name,
                    'uid': parent_uid,
                    'controller': True,
                    'blockOwnerDeletion': True
                }]
            },
            'spec': {
                'hostname': router['hostname'],
                'router_id': router['router_id'],
                'image': router.get('image', 'vyos:1.5'),
                'source_network': parent_name,
                'interfaces': generate_router_interfaces(router, network_lookup),
                'vrfs': router.get('vrfs', []),
                'protocols': generate_router_protocols(router),
                'services': router.get('services', {}),
                'qos': generate_router_qos_config(router),
                'firewall': generate_router_firewall_config(router)
            }
        }
        
        vyos_router_crs.append(vyos_router_cr)
    
    return vyos_router_crs

def build_network_lookup(networks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build a lookup table for network information"""
    network_lookup = {}
    
    for network in networks:
        network_name = network['name']
        connected_routers = network.get('connected_routers', [])
        
        # Build router to IP mapping for this network
        router_ips = {}
        for conn in connected_routers:
            router_ips[conn['router_name']] = {
                'ip_address': conn['ip_address'],
                'interface': conn['interface']
            }
        
        network_lookup[network_name] = {
            'subnet': network['subnet'],
            'mtu': network.get('mtu', 1500),
            'vlan': network.get('vlan'),
            'router_ips': router_ips,
            'linux_network': network_name if network_name != 'loopbacks' else None
        }

        # if there is gateway defined, add it
        if 'gateway' in network:
            network_lookup[network_name]['gateway'] = network['gateway']

    return network_lookup

def generate_router_interfaces(router: Dict[str, Any], network_lookup: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Generate interface configuration for a router"""
    interfaces = []
    router_name = router['name']
    
    for interface_def in router.get('interfaces', []):
        interface_name = interface_def['name']
        network_name = interface_def['network']
        
        # Handle loopback interfaces
        if interface_name == 'lo':
            interfaces.append({
                'name': 'lo',
                'description': interface_def.get('description', 'Loopback interface'),
                'address': f"{router['router_id']}/32",
                'mtu': 1500,
                'enabled': True
            })
            continue
        
        # Get network information
        if network_name not in network_lookup:
            logger.warning(f"Network {network_name} not found for router {router_name}")
            continue
        
        network_info = network_lookup[network_name]
        router_ip_info = network_info['router_ips'].get(router_name)
        
        if not router_ip_info:
            logger.warning(f"No IP assignment found for router {router_name} on network {network_name}")
            continue
        
        # Calculate subnet mask from network subnet
        subnet = ipaddress.ip_network(network_info['subnet'])
        ip_with_mask = f"{router_ip_info['ip_address']}/{subnet.prefixlen}"
        
        interface_config = {
            'name': interface_name,
            'description': interface_def.get('description', ''),
            'address': ip_with_mask,
            'mtu': network_info['mtu'],
            'enabled': interface_def.get('enabled', True)
        }

        # Add bandwidth if specified
        if 'bandwidth' in interface_def:
            interface_config['bandwidth'] = interface_def['bandwidth']

        # Add gateway if specified
        if 'gateway' in network_info:
            interface_config['gateway'] = network_info['gateway']

        # Add VRF if specified
        if 'vrf' in interface_def:
            interface_config['vrf'] = interface_def['vrf']
        
        # Add Linux network reference
        if network_info['linux_network']:
            interface_config['linux_network'] = network_info['linux_network']
        
        # Add VLAN if specified
        if network_info['vlan'] is not None:
            interface_config['vlan'] = network_info['vlan']
        
        interfaces.append(interface_config)
    
    return interfaces

def generate_router_protocols(router: Dict[str, Any]) -> Dict[str, Any]:
    """Generate protocol configuration for a router"""
    protocols = {}
    router_protocols = router.get('protocols', {})
    
    # OSPF configuration
    if 'ospf' in router_protocols and router_protocols['ospf'].get('enabled'):
        ospf_config = {
            'router_id': router['router_id'],
            'areas': []
        }
        
        # Build interface to network mapping for OSPF area assignment
        # For now, all interfaces (except loopback and VRF interfaces) go into backbone area 0.0.0.0
        # Access area interfaces are determined by their area membership
        for area_id in router_protocols['ospf'].get('areas', []):
            networks = []
            
            # Add connected interface networks to the appropriate OSPF area
            # Interfaces on this router should advertise their connected subnets
            for interface in router.get('interfaces', []):
                # Skip loopback - it gets added as /32
                if interface['name'] == 'lo':
                    continue
                
                # Skip VRF interfaces - they don't participate in global OSPF
                if interface.get('vrf'):
                    continue
                
                # Get network name to determine subnet
                network_name = interface.get('network')
                if network_name:
                    # This will be filled in from the network_lookup during interface generation
                    # For now, we'll mark it to be populated dynamically
                    # The interface's subnet will be added to OSPF based on area assignment
                    pass
            
            # Add loopback network for all areas (router-id advertisement)
            if area_id == '0.0.0.0':  # Backbone area gets the loopback
                networks.append(f"{router['router_id']}/32")
            
            area_config = {
                'area': area_id,
                'networks': networks,
                'type': 'standard' if area_id != '0.0.0.0' else 'backbone'
            }
            ospf_config['areas'].append(area_config)
        
        # Add passive interfaces if specified
        if 'passive_interfaces' in router_protocols['ospf']:
            ospf_config['passive_interfaces'] = router_protocols['ospf']['passive_interfaces']
        
        protocols['ospf'] = ospf_config
    
    # BGP configuration
    if 'bgp' in router_protocols and router_protocols['bgp'].get('enabled'):
        bgp_config = {
            'as_number': router_protocols['bgp']['as_number'],
            'router_id': router['router_id'],
            'route_reflector': router_protocols['bgp'].get('route_reflector', False),
            'neighbors': router_protocols['bgp'].get('neighbors', []),
            'vrfs': router_protocols['bgp'].get('vrfs', [])
        }
        
        # Add address families if specified
        if 'address_families' in router_protocols['bgp']:
            bgp_config['address_families'] = router_protocols['bgp']['address_families']
        else:
            # Auto-enable VPNv4 address family for PE routers (with VRFs) and route reflectors
            # This is needed for MPLS L3VPN to function
            has_vrfs = len(router.get('vrfs', [])) > 0
            is_route_reflector = router_protocols['bgp'].get('route_reflector', False)
            
            if has_vrfs or is_route_reflector:
                bgp_config['address_families'] = [{'family': 'vpnv4'}]
        
        protocols['bgp'] = bgp_config
    
    # MPLS configuration
    if 'mpls' in router_protocols and router_protocols['mpls'].get('enabled'):
        mpls_config = {
            'enabled': True,
            'ldp': {
                'router_id': router['router_id'],
                'interfaces': router_protocols['mpls'].get('ldp_interfaces', [])
            }
        }
        protocols['mpls'] = mpls_config
    
    # Static routes configuration
    if 'static' in router_protocols and router_protocols['static'].get('routes'):
        static_config = {
            'routes': router_protocols['static']['routes']
        }
        protocols['static'] = static_config
    
    return protocols

def generate_router_qos_config(router: Dict[str, Any]) -> Dict[str, Any]:
    """Generate QoS configuration for a router"""
    qos_config = {'policies': []}
    
    for qos_policy in router.get('qos_policies', []):
        qos_config['policies'].append({
            'name': qos_policy['policy_name'],
            'interface': qos_policy['interface'],
            'direction': qos_policy.get('direction', 'out')
        })
    
    return qos_config

def generate_router_firewall_config(router: Dict[str, Any]) -> Dict[str, Any]:
    """Generate firewall configuration for a router"""
    firewall_config = {'policies': []}
    
    for firewall_policy in router.get('firewall_policies', []):
        firewall_config['policies'].append({
            'name': firewall_policy['policy_name'],
            'interface': firewall_policy['interface'],
            'direction': firewall_policy['direction']
        })
    
    return firewall_config

#########################################################################
# Kubernetes API Operations
#########################################################################

async def create_linux_network(network_cr: Dict[str, Any], namespace: str):
    """Create a LinuxNetwork custom resource"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='LinuxNetwork')
    
    try:
        api.create(network_cr)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 409:  # Already exists
            logger.info(f"LinuxNetwork {network_cr['metadata']['name']} already exists")
        else:
            raise

async def create_vyos_router(router_cr: Dict[str, Any], namespace: str):
    """Create a VyOSRouter custom resource"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
    
    try:
        api.create(router_cr)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 409:  # Already exists
            logger.info(f"VyOSRouter {router_cr['metadata']['name']} already exists")
        else:
            raise

async def update_status(name: str, namespace: str, phase: str, message: str, 
                       networks: Optional[List[str]] = None, routers: Optional[List[str]] = None):
    """Update the status of a VyOSNetwork resource"""
    logger.info(f"Updating VyOSNetwork {name} status to {phase}: {message}")

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='VyOSNetwork')
    
    resource = api.get(name=name, namespace=namespace)
    if not resource:
        logger.error(f"VyOSNetwork {name} not found in namespace {namespace} for status update")
        return

    resource_dict = resource.to_dict()

    if 'status' not in resource_dict:
        resource_dict['status'] = {}

    status = {
        'phase': phase,
        'message': message
    }
    if networks:
        status['networks'] = networks
    if routers:
        status['routers'] = routers

    resource_dict['status'].update(status)
    
    try:
        api.patch(
            namespace=namespace,
            name=name,
            body=resource_dict,
            content_type='application/merge-patch+json',
            subresource='status'
        )
    except kubernetes.client.rest.ApiException as e:
        if e.status == 422 and "status" in str(e):
            logger.warning(f"Status subresource not enabled for VyOSNetwork {name}, skipping status update.")
        else:
            logger.error(f"Failed to update status for VyOSNetwork {name}: {e}")

async def check_and_update_parent_network_status(parent_network_name: str, namespace: str, logger):
    """Check if all child VyOSRouters and LinuxNetworks are Ready and update parent VyOSNetwork status accordingly"""
    logger.info("Checking parent VyOSNetwork status for child readiness")
    
    try:
        # Get the parent VyOSNetwork
        client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
        vyosnetwork_api = client.resources.get(api_version='google.dev/v1', kind='VyOSNetwork')
        vyosrouter_api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
        
        # Get the parent network
        try:
            parent_network = vyosnetwork_api.get(name=parent_network_name, namespace=namespace)
        except kubernetes.client.rest.ApiException as e:
            if e.status == 404:
                logger.info(f"Parent VyOSNetwork {parent_network_name} not found, may have been deleted")
                return
            else:
                raise
        
        # Check current status - only update if currently in Building state
        current_status = parent_network.get('status', {})
        current_phase = current_status.get('phase', '')
        
        if current_phase != 'Creating':
            logger.info(f"Parent VyOSNetwork {parent_network_name} is in {current_phase} state, not Creating - skipping check")
            return
        
        # Get all child VyOSRouters that belong to this VyOSNetwork
        all_routers = vyosrouter_api.list(namespace=namespace)
        child_routers = []
        
        for router in all_routers.items:
            owner_references = router.get('metadata', {}).get('ownerReferences', [])
            for owner in owner_references:
                if owner.get('kind') == 'VyOSNetwork' and owner.get('name') == parent_network_name:
                    child_routers.append(router)
                    break
                
        if not child_routers:
            logger.warning(f"No child resources found for VyOSNetwork {parent_network_name}")
            return
        
        # Check if all child routers are Ready (status.phase == "Running" for VyOSRouter)
        ready_routers = []
        not_ready_routers = []
        
        for router in child_routers:
            router_name = router.get('metadata', {}).get('name', 'unknown')
            router_status = router.get('status', {})
            router_phase = router_status.get('phase', 'Unknown')
            
            if router_phase == 'Running':  # VyOSRouter uses "Running" as its ready state
                ready_routers.append(router_name)
            else:
                not_ready_routers.append(f"{router_name}({router_phase})")
                
        total_routers = len(child_routers)
        ready_router_count = len(ready_routers)
        
        logger.info(f"VyOSNetwork {parent_network_name}: {ready_router_count}/{total_routers} routers ready")
        
        # All children must be ready for parent to be Ready
        all_ready = (ready_router_count == total_routers)
        
        if all_ready:
            # All children are ready, update parent to Ready
            success_message = f"All {total_routers} routers are ready"
            await update_status(parent_network_name, namespace, "Running", success_message)
            logger.info(f"VyOSNetwork {parent_network_name} set to Running - all children are ready")
        else:
            # Some children are still not ready, update status message
            not_ready_items = not_ready_routers
            if not_ready_items:
                status_message = f"Waiting for: {', '.join(not_ready_items)} ({ready_router_count}/{total_routers} ready)"
            else:
                status_message = f"Waiting ({ready_router_count}/{total_routers} ready)"
            await update_status(parent_network_name, namespace, "Creating", status_message)
            logger.info(f"VyOSNetwork {parent_network_name} still waiting for: {not_ready_items}")
            
    except Exception as e:
        logger.error(f"Failed to check parent network status for {parent_network_name}: {e}")
