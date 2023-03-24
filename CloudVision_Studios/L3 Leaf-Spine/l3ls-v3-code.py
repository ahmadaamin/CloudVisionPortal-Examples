<%
import ipaddress
import re
import time
from collections import Counter, OrderedDict
import tagsearch_python.tagsearch_pb2_grpc as tsgr
import tagsearch_python.tagsearch_pb2 as tspb
from arista.tag.v2.tag_pb2 import TagKey, \
    TagAssignmentKey
from arista.tag.v2.services import TagConfigServiceStub, \
    TagAssignmentConfigServiceStub, \
    TagConfigSetRequest, \
    TagAssignmentConfigSetRequest


fabric_variables = {
    "bgp_peer_groups": {
        "IPv4_UNDERLAY_PEERS": {
            "name": "IPv4-UNDERLAY-PEERS",
            "password": None
        },
        "MLAG_IPv4_UNDERLAY_PEER": {
            "name": "MLAG-IPv4-UNDERLAY-PEER",
            "password": None,
        },
        "EVPN_OVERLAY_PEERS": {
            "name": "EVPN-OVERLAY-PEERS",
            "password": None
        }
    },
    "bfd_multihop": {
        "interval": 300,
        "min_rx": 300,
        "multiplier": 3
    },
    "evpn_ebgp_multihop": 3,
    "evpn_hostflap_detection": {
        "enabled": False,
        "threshold": 5,
        "window": 180
    },
    "interface_descriptions":{
        "underlay_l3_ethernet_interfaces": "P2P_LINK_TO_{link['peer'].upper()}_{link['peer_interface']}",
        "underlay_l2_ethernet_interfaces": "TO_{link['peer'].upper()}_{link['peer_interface']}",
        "underlay_port_channel_interfaces": "{link['peer'].upper()}_Po{link.get('peer_channel_group_id')}",
        "router_id_interface": "EVPN_Overlay_Peering",
        "vtep_source_interface": "VTEP_VXLAN_Tunnel_Source",
        "mlag_ethernet_interfaces": "MLAG_{mlag_peer}_{mlag_peer_interface}",
        "mlag_port_channel_interface": "MLAG_PEER_{switch_facts['mlag_peer']}_Po{switch_facts['mlag_port_channel_id']}"
    },
    "p2p_interface_settings": []
}


platform_settings = {
    "jericho-fixed": {
        "regexes": [r'DCS-7280\w(R|R2)\D*-.+', r'DCS-7048T'],
        "reload_delay": {
            "mlag": 900,
            "non_mlag": 1020
        },
        "tcam_profile": "vxlan-routing",
        "info": "Configured in standard settings"
    },
    "jericho-chassis": {
        "regexes": [r'DCS-75\d\d'],
        "reload_delay": {
            "mlag": 900,
            "non_mlag": 1020
        },
        "tcam_profile": "vxlan-routing",
        "info": "Configured in standard settings"
    },
    "jericho2-fixed": {
        "regexes": [r'DCS-7280\w(R3)\D*-.+'],
        "reload_delay": {
            "mlag": 900,
            "non_mlag": 1020
        },
        "tcam_profile": None,
        "info": "Configured in standard settings"
    },
    "jericho2-chassis": {
        "regexes": [r'DCS-78\d\d'],
        "reload_delay": {
            "mlag": 900,
            "non_mlag": 1020
        },
        "tcam_profile": None,
        "info": "Configured in standard settings"
    },
    #Ahmad adding T3 Fixed and Chassis
    "trident3-fixed": {
        "regexes": [r'DCS-7050[STC]X3',r'CCS-72[02]XP'],
        "reload_delay": {
            "mlag": 300,
            "non_mlag": 330
        },
        "tcam_profile": None,
        "info": "Configured in standard settings",
        "trident_forwarding_table_partition": "flexible exact-match 16384 l2-shared 98304 l3-shared 131072"
    },
    "trident3-chassis": {
        "regexes": [r'DCS-73\d\dX3'],
        "reload_delay": {
            "mlag": 1200,
            "non_mlag": 1320
        },
        "tcam_profile": None,
        "info": "Configured in standard settings",
        "trident_forwarding_table_partition": "flexible exact-match 16384 l2-shared 98304 l3-shared 131072"
    },
    "default": {
        "regexes": [r'.+'],
        "reload_delay": {
            "mlag": 300,
            "non_mlag": 330
        },
        "tcam_profile": None,
        "info": "Configured in standard settings"
    }
}


jericho_platform_regexes = [
    r'7048T',
    r'7280',
    r'75\d\d',
    r'780\d'
]


veos_regex = r'(v|c)EOS(-)*(Lab)*'


lacp_mode_mapper = {
    "active": "active",
    "passive": "passive",
    "on (static)": "on"
}


def convert(text):
    return int(text) if text.isdigit() else text.lower()


def alphanum_key(key):
    return [convert(c) for c in re.split('([0-9]+)', str(key))]


def natural_sort(iterable):
    if iterable is None:
        return list()
    return sorted(iterable, key=alphanum_key)


def string_to_list(string_to_convert):
    numbers = []
    segments = [segment.strip() for segment in string_to_convert.split(",") if segment.strip() != ""]
    for segment in segments:
        if "-" in segment:
            for i in range(int(segment.split("-")[0]), int(segment.split("-")[1]) + 1):
                if i not in numbers:
                    numbers.append(i)
        else:
            if int(segment) not in numbers:
                numbers.append(int(segment))
    return numbers


def get_tag_values_applied_to_device(tag_assignment_key):
    '''
    Returns all tags applied to a device that match the label of the input tag_assignment_key

    Args:
        tag_assignment_key: TagAssignmentKey object with the label field set
    '''
    label = tag_assignment_key.label.value
    value = tag_assignment_key.value.value
    device_id = tag_assignment_key.device_id.value
    workspace_id = tag_assignment_key.workspace_id.value
    # Create tagstub
    tsclient = ctx.getApiClient(tsgr.TagSearchStub)

    matching_tags = []

    # Create TagValueSearchRequest
    tvsr = tspb.TagValueSearchRequest(
        label=label,
        workspace_id=workspace_id,
        topology_studio_request=True
    )
    for tag in tsclient.GetTagValueSuggestions(tvsr).tags:
        query = f"{tag.label}:\"{tag.value}\" AND device:{device_id}"
        tagmr = tspb.TagMatchRequestV2(
            query=query,
            workspace_id=workspace_id,
            topology_studio_request=True
        )
        tagmresp = tsclient.GetTagMatchesV2(tagmr)
        for match in tagmresp.matches:
            if match.device.device_id == device_id:
                matching_tags.append(tag)

    return matching_tags


def get_tag_value(device_id=None, label=None, workspace_id=None):
    tag_assignment_key = TagAssignmentKey()
    tag_assignment_key.element_type = 1
    if workspace_id is not None:
        tag_assignment_key.workspace_id.value = workspace_id
    if device_id is not None:
        tag_assignment_key.device_id.value = device_id
    if label is not None:
        tag_assignment_key.label.value = label
    tag_values = get_tag_values_applied_to_device(tag_assignment_key)
    if len(tag_values) > 0:
        return tag_values[0].value


def get_tag_values(device_id=None, label=None, workspace_id=None):
    tag_assignment_key = TagAssignmentKey()
    tag_assignment_key.element_type = 1
    if workspace_id is not None:
        tag_assignment_key.workspace_id.value = workspace_id
    if device_id is not None:
        tag_assignment_key.device_id.value = device_id
    if label is not None:
        tag_assignment_key.label.value = label
    tag_values = get_tag_values_applied_to_device(tag_assignment_key)
    if len(tag_values) > 0:
        return [tag_value.value for tag_value in tag_values]


def create_tag(tag_key):
    '''
    tag_key is a TagKey
    '''
    tcsr = TagConfigSetRequest()
    tcsr.value.key.workspace_id.value = tag_key.workspace_id.value
    tcsr.value.key.element_type = tag_key.element_type
    tcsr.value.key.label.value = tag_key.label.value
    tcsr.value.key.value.value = tag_key.value.value
    client = ctx.getApiClient(TagConfigServiceStub)
    client.Set(tcsr)


def apply_tag(tag_assignment_key):
    '''
    tag_assignment_key is a TagAssignmentKey
    '''
    tacsr = TagAssignmentConfigSetRequest()
    tacsr.value.key.workspace_id.value = tag_assignment_key.workspace_id.value
    tacsr.value.key.element_type = tag_assignment_key.element_type
    tacsr.value.key.label.value = tag_assignment_key.label.value
    tacsr.value.key.value.value = tag_assignment_key.value.value
    tacsr.value.key.device_id.value = tag_assignment_key.device_id.value
    tacsr.value.key.interface_id.value = tag_assignment_key.interface_id.value
    tacsr.value.remove.value = False
    client = ctx.getApiClient(TagAssignmentConfigServiceStub)
    client.Set(tacsr)


def remove_tag(tag_assignment_key):
    '''
    tag_assignment_key is a TagAssignmentKey
    '''
    tacsr = TagAssignmentConfigSetRequest()
    tacsr.value.key.workspace_id.value = tag_assignment_key.workspace_id.value
    tacsr.value.key.element_type = tag_assignment_key.element_type
    tacsr.value.key.label.value = tag_assignment_key.label.value
    tacsr.value.key.value.value = tag_assignment_key.value.value
    tacsr.value.key.device_id.value = tag_assignment_key.device_id.value
    tacsr.value.key.interface_id.value = tag_assignment_key.interface_id.value
    tacsr.value.remove.value = True
    client = ctx.getApiClient(TagAssignmentConfigServiceStub)
    client.Set(tacsr)


def remove_all_tag_values(tag_label, device_id, workspace_id, value=None):
    '''
    Removes all tags with the input tag label matchiing the input device_id
    If a value is supplied, only tags matching the label and value will be removed from the device
    '''
    tag_assignment_key = TagAssignmentKey()
    tag_assignment_key.workspace_id.value = workspace_id
    tag_assignment_key.element_type = 1
    tag_assignment_key.device_id.value = device_id
    tag_assignment_key.label.value = tag_label
    # tag_assignment_key.value.value = None
    matching_tags = get_tag_values_applied_to_device(tag_assignment_key)
    # If there are any mlag_configuration.peer_link tags applied
    if len(matching_tags) > 0:
        # Remove tags
        for tag in matching_tags:
            tak_to_remove = TagAssignmentKey()
            tak_to_remove.workspace_id.value = tag_assignment_key.workspace_id.value
            tak_to_remove.element_type = 1
            tak_to_remove.device_id.value = tag_assignment_key.device_id.value
            tak_to_remove.label.value = tag_assignment_key.label.value
            tak_to_remove.value.value = tag.value
            if value is None:
                remove_tag(tak_to_remove)
            else:
                if tag.value == value:
                    remove_tag(tak_to_remove)


def update_device_tag(tag_assignment_key, multiple_values=False):
    """
    tag_assignment_key is a TagAssignmentKey that you want to update the device with
    """
    applied_tags = get_tag_values_applied_to_device(tag_assignment_key)
    already_assigned = False
    for tag in applied_tags:
        if tag.value != tag_assignment_key.value.value:
            if multiple_values is True:
                continue
            tak_to_remove = TagAssignmentKey()
            tak_to_remove.workspace_id.value = tag_assignment_key.workspace_id.value
            tak_to_remove.element_type = 1
            tak_to_remove.device_id.value = tag_assignment_key.device_id.value
            tak_to_remove.label.value = tag_assignment_key.label.value
            tak_to_remove.value.value = tag.value
            remove_tag(tak_to_remove)
        else:
            already_assigned = True

    if already_assigned:
        return

    # Create desired tag
    tag_key = TagKey()
    tag_key.workspace_id.value = workspace_id
    tag_key.element_type = 1
    tag_key.label.value = tag_assignment_key.label.value
    tag_key.value.value = tag_assignment_key.value.value
    create_tag(tag_key)

    # Apply desired tag
    apply_tag(tag_assignment_key)


def update_tags(switch_facts):
    device_id = switch_facts['serial_number']
    # NodeId tag
    # Create/Update/Apply the NodeId tag
    tag_assignment_key = TagAssignmentKey()
    tag_assignment_key.workspace_id.value = workspace_id
    tag_assignment_key.element_type = 1
    tag_assignment_key.device_id.value = device_id
    tag_assignment_key.label.value = "NodeId"
    tag_assignment_key.value.value = str(switch_facts['id'])
    update_device_tag(tag_assignment_key)
    # Update network services tags
    if switch_facts['network_services'].get('l2'):
        # Create/Apply the following tag 'NetworkServices:L2'
        tag_assignment_key = TagAssignmentKey()
        tag_assignment_key.workspace_id.value = workspace_id
        tag_assignment_key.element_type = 1
        tag_assignment_key.device_id.value = device_id
        tag_assignment_key.label.value = "NetworkServices"
        tag_assignment_key.value.value = "L2"
        update_device_tag(tag_assignment_key, multiple_values=True)
    else:
        # Remove the following tag 'NetworkServices:L2'
        remove_all_tag_values("NetworkServices", device_id, workspace_id, value="L2")
    if switch_facts['network_services'].get('l3'):
        # Create/Apply the following tag 'NetworkServices:L3'
        tag_assignment_key = TagAssignmentKey()
        tag_assignment_key.workspace_id.value = workspace_id
        tag_assignment_key.element_type = 1
        tag_assignment_key.device_id.value = device_id
        tag_assignment_key.label.value = "NetworkServices"
        tag_assignment_key.value.value = "L3"
        update_device_tag(tag_assignment_key, multiple_values=True)
    else:
        # Remove the following tag 'NetworkServices:L3'
        remove_all_tag_values("NetworkServices", device_id, workspace_id, value="L3")
    # Update mlag_configuration.peer_link
    if switch_facts.get('mlag'):
        mlag_peer_link = f"Port-Channel{switch_facts['mlag_port_channel_id']}"
        tag_assignment_key = TagAssignmentKey()
        tag_assignment_key.workspace_id.value = workspace_id
        tag_assignment_key.element_type = 1
        tag_assignment_key.device_id.value = device_id
        tag_assignment_key.label.value = "mlag_configuration.peer_link"
        tag_assignment_key.value.value = str(mlag_peer_link)
        update_device_tag(tag_assignment_key)
    else:
        remove_all_tag_values("mlag_configuration.peer_link", device_id, workspace_id)

    # Update routing tags
    bgp_tags = {"router_bgp.as": switch_facts.get('bgp_as'), "router_bgp.router_id": switch_facts.get('router_id')}
    if switch_facts.get('underlay_router') \
        and (switch_facts['underlay_routing_protocol'] == "bgp"
            or switch_facts['overlay_routing_protocol'] == "bgp"):
        # Set bgp as and router id tags
        for label, value in bgp_tags.items():
            tag_assignment_key = TagAssignmentKey()
            tag_assignment_key.workspace_id.value = workspace_id
            tag_assignment_key.element_type = 1
            tag_assignment_key.device_id.value = device_id
            tag_assignment_key.label.value = str(label)
            tag_assignment_key.value.value = str(value)
            update_device_tag(tag_assignment_key)
    else:
        # Remove possible bgp tags
        for label, value in bgp_tags.items():
            # Remove tags with same label that don't match proper value
            if value is None:
                remove_all_tag_values(str(label), device_id, workspace_id)
    # Update Vtep tag
    if switch_facts.get('vtep'):
        tag_assignment_key = TagAssignmentKey()
        tag_assignment_key.workspace_id.value = workspace_id
        tag_assignment_key.element_type = 1
        tag_assignment_key.device_id.value = device_id
        tag_assignment_key.label.value = "Vtep"
        tag_assignment_key.value.value = "True"
        update_device_tag(tag_assignment_key)
    else:
        remove_all_tag_values("Vtep", device_id, workspace_id)
    return


def merge_multilane_interfaces(switch_facts):
    '''
    Combines ethernet interface lanes that share common LLDP neighbor
    switch_facts.x_interfaces looks like {
        "EthernetX": {
            "neighborId": peer_device.id,
            "neighborHostname": peer_device.hostName,
            "neighborInterface": peer_interface.name
        }
    }
    '''
    # If we see the same neighbor on multiple lanes of an interface - merge the interface lanes
    iface_types = [
        switch_facts['mlag_peer_link_interfaces'],
        switch_facts['uplink_interfaces'],
        switch_facts['downlink_interfaces']
    ]
    for ifaces in iface_types:
        sorted_ifaces = natural_sort(ifaces.keys())
        for iface in sorted_ifaces:
            # Check to see if iface has already been removed from interfaces
            if not ifaces.get(iface):
                continue
            # Get number of lanes on ethernet interface by counting slashes in interface name
            slash_count = alphanum_key(iface).count("/")
            if slash_count == 0:
                continue
            # Set the iface group to the all but the last number in the interface name
            # i.e. Ethernet3/2/1 and Ethernet3/2/2 would both be in group would be Ethernet3/2
            iface_group = "".join([str(i) for i in alphanum_key(iface)[: slash_count * 2]])
            # Compare rest of the interfaces in that group to see if they have the same neighbor
            for member_iface in sorted_ifaces:
                potential_iface_group = "".join([str(i) for i in alphanum_key(member_iface)[: slash_count * 2]])
                if potential_iface_group == iface_group and iface != member_iface:
                    try:
                        if ifaces[iface]['neighborId'] == ifaces[member_iface]['neighborId']:
                            del ifaces[member_iface]
                    except KeyError:
                        continue
    return switch_facts


def get_max_spines(switch_facts, data_center_resolver):
    data_center = data_center_resolver.resolve(device=switch_facts['serial_number'])['dataCenter']
    pod = data_center['pods'].resolve(device=switch_facts['serial_number'])['pod']
    if pod.get('maximums') and pod['maximums'].get('maxSpines'):
        return pod['maximums']['maxSpines']
    # Get highest spine id in pod
    max_node_id = 0
    for switch_in_my_data_center_facts in switches_in_my_data_center.values():
        if switch_in_my_data_center_facts['type'] == "spine" and \
                switch_in_my_data_center_facts['pod'] == switch_facts['pod'] and \
                switch_in_my_data_center_facts['id'] > max_node_id:
            max_node_id = switch_in_my_data_center_facts['id']

    return max_node_id


def get_max_super_spines(switch_facts, data_center_resolver):
    data_center = data_center_resolver.resolve(device=switch_facts['serial_number'])['dataCenter']
    pod = data_center['pods'].resolve(device=switch_facts['serial_number'])['pod']
    if pod.get('maximums') and pod['maximums'].get('maxSuperSpines'):
        return pod['maximums']['maxSuperSpines']
    # Get highest super spine id in datacenter
    max_node_id = 0
    for switch_in_my_data_center_facts in switches_in_my_data_center.values():
        if switch_in_my_data_center_facts['type'] == "super_spine" and \
                switch_in_my_data_center_facts['data_center'] == switch_facts['data_center'] and \
                switch_in_my_data_center_facts['id'] > max_node_id:
            max_node_id = switch_in_my_data_center_facts['id']
    return max_node_id


def get_max_parallel_uplinks_to_spines(switch_facts, data_center_resolver):
    '''
    Uses downlinks on spines to get max parallel uplinks from leafs to spines
    This should save some time when it comes to config generation so the template doesn't need to
    get interface info for all leaf switches
    '''
    data_center = data_center_resolver.resolve(device=switch_facts['serial_number'])['dataCenter']
    pod = data_center['pods'].resolve(device=switch_facts['serial_number'])['pod']
    if pod.get('maximums') and pod['maximums'].get('maximumNumberOfParallelConnections'):
        return pod['maximums']['maximumNumberOfParallelConnections']
    # Get highest super spine id in datacenter
    switch_max_parallel_links = []
    for switch_in_my_data_center_facts in switches_in_my_data_center.values():
        if switch_in_my_data_center_facts['type'] == "spine" and \
                switch_in_my_data_center_facts['pod'] == switch_facts['pod']:
            downlink_neighbors = [
                iface.get('neighborId') for iface in switch_in_my_data_center_facts['downlink_interfaces'].values()
            ]
            if len(Counter(downlink_neighbors).most_common(1)) > 0:
                switch_max_parallel_links.append(Counter(downlink_neighbors).most_common(1)[0][1])

    if len(switch_max_parallel_links) == 0:
        return 1

    return max(switch_max_parallel_links)


def get_max_parallel_uplinks_to_super_spines(switch_facts, data_center_resolver):
    '''
    Uses uplinks on spines to get max parallel uplinks from spines to super spines
    This should save some time when it comes to config generation so the template doesn't need to
    get interface info for all leaf switches
    '''
    data_center = data_center_resolver.resolve(device=switch_facts['serial_number'])['dataCenter']
    pod = data_center['pods'].resolve(device=switch_facts['serial_number'])['pod']
    if pod.get('maximums') and pod['maximums'].get('maximumParallelSpineUplinks'):
        return pod['maximums']['maximumParallelSpineUplinks']
    # Get highest super spine id in datacenter
    switch_max_parallel_links = []
    for switch_in_my_data_center_facts in switches_in_my_data_center.values():
        if switch_in_my_data_center_facts['type'] == "spine" and \
                switch_in_my_data_center_facts['pod'] == switch_facts['pod']:
            uplink_neighbors = [
                iface.get('neighborId') for iface in switch_in_my_data_center_facts['uplink_interfaces'].values()
            ]
            if len(Counter(uplink_neighbors).most_common(1)) > 0:
                switch_max_parallel_links.append(Counter(uplink_neighbors).most_common(1)[0][1])

    if len(switch_max_parallel_links) == 0:
        return 0

    return max(switch_max_parallel_links)


def set_maximums(switch_facts):
    max_spines = 0
    max_super_spines = 0
    max_parallel_uplinks_to_spines = 1
    max_parallel_uplinks_to_super_spines = 1
    for sf in switches_in_my_data_center.values():
        if sf['type'] in ['spine']:
            # Get max spines/super spines and set max_uplink_switches
            max_spines = get_max_spines(sf, dataCenters)
            max_super_spines = get_max_super_spines(sf, dataCenters)
            # Get max parallel uplinks for l3leafs/spines and set max_parallel_uplinks
            max_parallel_uplinks_to_spines = get_max_parallel_uplinks_to_spines(sf, dataCenters)
            max_parallel_uplinks_to_super_spines = get_max_parallel_uplinks_to_super_spines(sf, dataCenters)
            break
    if switch_facts['type'] in ["l3leaf", "spine"]:
        switch_facts['max_spines'] = max_spines
        switch_facts['max_super_spines'] = max_super_spines
        switch_facts['max_parallel_uplinks_to_spines'] = max_parallel_uplinks_to_spines
        switch_facts['max_parallel_uplinks_to_super_spines'] = max_parallel_uplinks_to_super_spines
        if switch_facts['type'] == "l3leaf":
            switch_facts['max_uplink_switches'] = switch_facts['max_spines']
            switch_facts['max_parallel_uplinks'] = switch_facts['max_parallel_uplinks_to_spines']
        else:
            switch_facts['max_uplink_switches'] = switch_facts['max_super_spines']
            switch_facts['max_parallel_uplinks'] = switch_facts['max_parallel_uplinks_to_super_spines']


def get_interfaces_info(switch_facts):
    device_id = switch_facts['serial_number']
    uplink_interfaces = {}
    downlink_interfaces = {}
    mlag_peer_link_interfaces = {}
    for i in ctx.topology.getDevices(deviceIds=[device_id])[0].getInterfaces():
        peer_device, peer_interface = i.getPeerInfo()
        if peer_device is not None and "Ethernet" in i.name:
            if switches_in_my_data_center.get(peer_device.id):
                neighbor = switches_in_my_data_center.get(peer_device.id)
                # Spine case
                if switch_facts['type'] == "spine":
                    if neighbor['type'] == "super_spine":
                        uplink_interfaces[i.name] = {
                            "neighborId": peer_device.id,
                            "neighborHostname": peer_device.hostName,
                            "neighborInterface": peer_interface.name
                        }
                    elif neighbor['type'] == "l3leaf":
                        downlink_interfaces[i.name] = {
                            "neighborId": peer_device.id,
                            "neighborHostname": peer_device.hostName,
                            "neighborInterface": peer_interface.name
                        }
                    elif neighbor['type'] == "spine":
                        mlag_peer_link_interfaces[i.name] = {
                            "neighborId": peer_device.id,
                            "neighborHostname": peer_device.hostName,
                            "neighborInterface": peer_interface.name
                        }
                # L3 Leaf case
                elif switch_facts['type'] == "l3leaf":
                    if neighbor['type'] == "spine":
                        uplink_interfaces[i.name] = {
                            "neighborId": peer_device.id,
                            "neighborHostname": peer_device.hostName,
                            "neighborInterface": peer_interface.name
                        }
                    elif neighbor['type'] == "l2leaf":
                        downlink_interfaces[i.name] = {
                            "neighborId": peer_device.id,
                            "neighborHostname": peer_device.hostName,
                            "neighborInterface": peer_interface.name
                        }
                    elif neighbor['type'] == "l3leaf":
                        mlag_peer_link_interfaces[i.name] = {
                            "neighborId": peer_device.id,
                            "neighborHostname": peer_device.hostName,
                            "neighborInterface": peer_interface.name
                        }
                # L2 Leaf case
                elif switch_facts['type'] == "l2leaf":
                    if neighbor['type'] == "l3leaf":
                        uplink_interfaces[i.name] = {
                            "neighborId": peer_device.id,
                            "neighborHostname": peer_device.hostName,
                            "neighborInterface": peer_interface.name
                        }
                    if neighbor['type'] == "l2leaf":
                        mlag_peer_link_interfaces[i.name] = {
                            "neighborId": peer_device.id,
                            "neighborHostname": peer_device.hostName,
                            "neighborInterface": peer_interface.name
                        }
                # Super Spine case
                elif switch_facts['type'] == "super_spine":
                    if neighbor['type'] == "spine":
                        downlink_interfaces[i.name] = {
                            "neighborId": peer_device.id,
                            "neighborHostname": peer_device.hostName,
                            "neighborInterface": peer_interface.name
                        }

    return OrderedDict(natural_sort(uplink_interfaces.items())), \
        OrderedDict(natural_sort(downlink_interfaces.items())), \
        OrderedDict(natural_sort(mlag_peer_link_interfaces.items()))


def set_bgp_as_from_studio_input(switch_facts, data_center_resolver):
    if switch_facts['type'] not in ['l3leaf']:
        return switch_facts
    # Process Studio inputs
    data_center = data_center_resolver.resolve(device=switch_facts['serial_number'])['dataCenter']
    pod = data_center['pods'].resolve(device=switch_facts['serial_number'])['pod']
    l3_leaf_domain = pod['LeafDomains'].resolve(device=switch_facts['serial_number'])['l3LeafDomain']
    if l3_leaf_domain is not None:
        bgp_as = l3_leaf_domain['asn']
    else:
        bgp_as = None
    if switch_facts['id'] and bgp_as is not None and bgp_as != 0:
        switch_facts['bgp_as'] = bgp_as
    return switch_facts


def get_router_id(switch_facts):
    router_id_subnet = switch_facts['loopback_ipv4_pool']
    switch_id = switch_facts['id']
    offset = switch_facts['loopback_ipv4_offset']
    return list(ipaddress.ip_network(router_id_subnet).hosts())[(switch_id - 1) + offset]


def get_mlag_ip(switch_facts, mlag_peer_ipv4_pool, mlag_subnet_mask, mlag_role):
    mlag_subnet = ipaddress.ip_network(mlag_peer_ipv4_pool)
    assert mlag_subnet.prefixlen <= mlag_subnet_mask, \
        f"MLAG Subnet Mask configured {mlag_subnet_mask} is " \
        f"less than MLAG subnet {mlag_peer_ipv4_pool} prefix {mlag_subnet.prefixlen}"

    if mlag_subnet.prefixlen != mlag_subnet_mask:
        # mlag_subnet = list(mlag_subnet.subnets(new_prefix=mlag_subnet_mask))[int(switch_facts['mlag_primary_id']) - 1]
        mlag_subnet = list(mlag_subnet.subnets(new_prefix=mlag_subnet_mask))[int(switch_facts['group_index'])]
    if mlag_role == "primary":
        return list(mlag_subnet.hosts())[0]
    elif mlag_role == "secondary":
        return list(mlag_subnet.hosts())[1]
    return


def get_vtep_loopback(switch_facts):
    vtep_loopback_subnet = switch_facts['vtep_loopback_ipv4_pool']
    if switch_facts.get('mlag_primary_id'):
        switch_id = switch_facts['mlag_primary_id'] - 1
    else:
        switch_id = switch_facts['id'] - 1
    # AVD uses the method above to set vtep loopback but we're just using group index for now
    switch_id = switch_facts['group_index']
    return list(ipaddress.ip_network(vtep_loopback_subnet).hosts())[switch_id]


def get_p2p_uplinks_ip(switch_facts, uplink_switch_facts, uplink_switch_index):
    if switch_facts.get('type') not in ["l3leaf", "spine"] or \
            switch_facts['uplink_ipv4_pool'] is None or \
            switch_facts['uplink_ipv4_subnet_mask'] is None:
        return
    uplink_ipv4_pool = switch_facts['uplink_ipv4_pool']
    uplink_subnet_mask = switch_facts['uplink_ipv4_subnet_mask']
    uplink_offset = switch_facts['uplink_offset'] if switch_facts.get('uplink_offset') else 0
    switch_id = switch_facts['id']
    uplink_switch_id = uplink_switch_facts['id']
    max_uplink_switches = switch_facts['max_uplink_switches']
    max_parallel_uplinks = switch_facts['max_parallel_uplinks']
    if len(uplink_ipv4_pool) > 1:
        uplink_ipv4_pool = uplink_ipv4_pool[uplink_switch_id - 1]
        uplink_switch_id = 1
        max_uplink_switches = 1
        uplink_switches_seen_previously = switch_facts['uplink_switches_ids'][:uplink_switch_index]
        uplink_switch_index = uplink_switches_seen_previously.count(uplink_switch_facts['serial_number'])
    else:
        uplink_ipv4_pool = uplink_ipv4_pool[0]
    # Valid subnet checks
    assert uplink_subnet_mask >= ipaddress.ip_network(uplink_ipv4_pool).prefixlen, \
        f"Uplink subnet {uplink_ipv4_pool} has prefix length " \
        f"{ipaddress.ip_network(uplink_ipv4_pool).prefixlen} greater than " \
        f"Underlay Fabric Subnet Mask {uplink_subnet_mask}."
    child_subnets = list(ipaddress.ip_network(uplink_ipv4_pool).subnets(new_prefix=uplink_subnet_mask))
    max_leafs_possible = (len(child_subnets) - (uplink_switch_index) + uplink_offset) / \
        (max_uplink_switches * max_parallel_uplinks + (uplink_switch_index) + uplink_offset) + 1
    assert len(child_subnets) > \
        (switch_id - 1) * max_uplink_switches * max_parallel_uplinks + (uplink_switch_index) + uplink_offset, \
        f"Not enough subnets in uplink pool {uplink_ipv4_pool} to allocate addresses for more than " \
        f"{int(max_leafs_possible)} leafs with {max_parallel_uplinks} parallel uplinks"
    child_subnet = child_subnets[(switch_id - 1) * max_uplink_switches * max_parallel_uplinks
                                + (uplink_switch_index) + uplink_offset]
    return list(child_subnet.hosts())[1]


def get_p2p_uplinks_peer_ip(switch_facts, uplink_switch_facts, uplink_switch_index):
    if switch_facts.get('type') not in ["l3leaf", "spine"] or \
            switch_facts['uplink_ipv4_pool'] is None or \
            switch_facts['uplink_ipv4_subnet_mask'] is None:
        return
    uplink_ipv4_pool = switch_facts['uplink_ipv4_pool']
    uplink_subnet_mask = switch_facts['uplink_ipv4_subnet_mask']
    uplink_offset = switch_facts['uplink_offset'] if switch_facts.get('uplink_offset') else 0
    switch_id = switch_facts['id']
    uplink_switch_id = uplink_switch_facts['id']
    max_uplink_switches = switch_facts['max_uplink_switches']
    max_parallel_uplinks = switch_facts['max_parallel_uplinks']
    if len(uplink_ipv4_pool) > 1:
        uplink_ipv4_pool = uplink_ipv4_pool[uplink_switch_id - 1]
        uplink_switch_id = 1
        max_uplink_switches = 1
        uplink_switches_seen_previously = switch_facts['uplink_switches_ids'][:uplink_switch_index]
        uplink_switch_index = uplink_switches_seen_previously.count(uplink_switch_facts['serial_number'])
    else:
        uplink_ipv4_pool = uplink_ipv4_pool[0]
    # Valid subnet checks
    assert uplink_subnet_mask >= ipaddress.ip_network(uplink_ipv4_pool).prefixlen, \
        f"Uplink subnet {uplink_ipv4_pool} has prefix len {ipaddress.ip_network(uplink_ipv4_pool).prefixlen}" \
        f" greater than Underlay Fabric Subnet Mask {uplink_subnet_mask}"
    child_subnets = list(ipaddress.ip_network(uplink_ipv4_pool).subnets(new_prefix=uplink_subnet_mask))
    max_leafs_possible = (len(child_subnets) - (uplink_switch_index) + uplink_offset) / \
        (max_uplink_switches * max_parallel_uplinks + (uplink_switch_index) + uplink_offset) + 1
    assert len(child_subnets) > \
        (switch_id - 1) * max_uplink_switches * max_parallel_uplinks + (uplink_switch_index) + uplink_offset, \
        f"Not enough subnets in uplink pool {uplink_ipv4_pool} to allocate addresses for more than " \
        f"{int(max_leafs_possible)} leafs with {max_parallel_uplinks} parallel uplinks"
    child_subnet = child_subnets[(switch_id - 1) * max_uplink_switches * max_parallel_uplinks
                                + (uplink_switch_index) + uplink_offset]
    return list(child_subnet.hosts())[0]


def set_topology_facts(switch_facts):
    topology_facts = {
        "links": {}
    }
    if switch_facts['uplink_type'] == "p2p":
        for i, uplink_interface in enumerate(switch_facts['uplink_interfaces'].keys()):
            link = {}
            uplink_switch_id = switch_facts['uplink_switches_ids'][i]
            uplink_switch_facts = switches_in_my_data_center.get(uplink_switch_id)
            if uplink_switch_facts is None:
                continue
            link['peer_id'] = uplink_switch_facts['serial_number']
            link['peer'] = uplink_switch_facts['hostname']
            link['peer_interface'] = switch_facts['uplink_switch_interfaces'][i]
            link['peer_type'] = uplink_switch_facts['type']
            link['peer_bgp_as'] = uplink_switch_facts.get('bgp_as')
            link['type'] = "underlay_p2p"
            link['ip_address'] = str(get_p2p_uplinks_ip(switch_facts, uplink_switch_facts, i))
            link['peer_ip_address'] = str(get_p2p_uplinks_peer_ip(switch_facts, uplink_switch_facts, i))
            link['subnet_mask'] = switch_facts['uplink_ipv4_subnet_mask']
            #Ahmad PIM enabled on uplink interfaces
            if switch_facts.get('underlay_multicast'):
                link['underlay_multicast'] = True
            else:
                link['underlay_multicast'] = False
            topology_facts['links'][uplink_interface] = link

    elif switch_facts['uplink_type'] == "port-channel":
        for i, uplink_interface in enumerate(switch_facts['uplink_interfaces'].keys()):
            link = {}
            uplink_switch_id = switch_facts['uplink_switches_ids'][i]
            uplink_switch_facts = switches_in_my_data_center.get(uplink_switch_id)
            if uplink_switch_facts is None:
                continue
            neighbor_switch = uplink_switch_facts  # Friendlier variable name for creating custom interface description
            link['peer_id'] = uplink_switch_facts['serial_number']
            link['peer'] = uplink_switch_facts['hostname']
            link['peer_interface'] = switch_facts['uplink_switch_interfaces'][i]
            link['peer_type'] = uplink_switch_facts['type']
            link['type'] = "underlay_l2"
            if uplink_switch_facts.get('mlag') is not None and uplink_switch_facts.get('mlag') is True:
                link['channel_description'] = eval(f"f\"{fabric_variables['interface_descriptions']['underlay_port_channel_interfaces']}\"")
            if switch_facts.get('mlag') is not None and switch_facts.get('mlag') is True:
                link['peer_channel_description'] = eval(f"f\"{fabric_variables['interface_descriptions']['underlay_port_channel_interfaces']}\"")
            if switch_facts.get('mlag_role') is not None and switch_facts['mlag_role'] == "secondary":
                mlag_peer_switch_facts = switches_in_my_data_center[switch_facts['mlag_peer_serial_number']]
                link['channel_group_id'] = "".join(
                    re.findall(r'\d', list(mlag_peer_switch_facts['uplink_interfaces'].keys())[0])
                )
                link['peer_channel_group_id'] = "".join(
                    re.findall(r'\d', mlag_peer_switch_facts['uplink_switch_interfaces'][0])
                )
            else:
                link['channel_group_id'] = "".join(
                    re.findall(r'\d', list(switch_facts['uplink_interfaces'].keys())[0])
                )
                link['peer_channel_group_id'] = "".join(
                    re.findall(r'\d', switch_facts['uplink_switch_interfaces'][0])
                )
            topology_facts['links'][uplink_interface] = link

    switch_facts['topology'] = topology_facts
    return switch_facts


def set_switch_facts(switch_facts, data_center_resolver):
    device_id = switch_facts['serial_number']
    # Process Studio inputs
    try:
        data_center = data_center_resolver.resolve(device=device_id)['dataCenter']
        pod = data_center['pods'].resolve(device=device_id)['pod']
        super_spine_plane = data_center['superSpinePlanes'].resolve(device=device_id)['superSpinePlane']
        user_input_platform_settings = data_center['platformSettingsResolver'].resolve(
            device=device_id)['platformSettingsGroup']
    except Exception:
        return

    # Parse user_input_platform_settings and set switch_platform settings
    if user_input_platform_settings is not None:
        switch_facts['platform_settings'] = {
            "reload_delay": {
                "mlag": user_input_platform_settings['reloadDelays']['mlagReloadDelay'],
                "non_mlag": user_input_platform_settings['reloadDelays']['nonMlagReloadDelay']
            },
            "tcam_profile": user_input_platform_settings['tcamProfile'],
            "eos_cli": user_input_platform_settings['eosCli'],
            "info": "Configured in custom settings"
        }

    if switch_facts.get('platform_settings') is None:
        for platform, settings in platform_settings.items():
            # Skip default as this will be applied at the end
            # to any switch that doesn't match any other platform
            if platform == "default":
                continue
            # check to see if any default platform regex is matched
            for regex in settings['regexes']:
                if re.search(regex, switch_facts['platform'], re.IGNORECASE):
                    switch_facts['platform_settings'] = settings

    # If no platform setting is matched, set to default
    if switch_facts.get('platform_settings') is None:
        switch_facts['platform_settings'] = platform_settings['default']

    # Set values based on Pod level inputs
    if pod is not None:
        # Get l3leaf and l2leaf resolvers
        l3_leaf_domain = pod['LeafDomains'].resolve(device=device_id)['l3LeafDomain']
        l2_leaf_domain = pod['l2LeafDomains'].resolve(device=device_id)['l2LeafDomain']

    # Get fabric details
    # Set underlay and overlay routing protocol
    if switch_facts['underlay_router'] is True:
        if pod is not None:
            switch_facts['underlay_routing_protocol'] = pod['underlayRouting']['underlayRoutingProtocol'].lower()
            if pod['overlayDetails'].get('vxlanOverlay'):
                switch_facts['overlay_routing_protocol'] = "bgp"
        # Accounts for super-spines; super-spine routing protocols are set later
        # based on first downstream spine's routing protocols
        else:
            switch_facts['underlay_routing_protocol'] = ""

    # Get spanning tree details
    if switch_facts['network_services']['l2'] is True:
        switch_facts['spanning_tree_mode'] = pod['spanningTreeMode'].lower()

    # Get virtual router mac
    if switch_facts['network_services']['l2'] is True and \
            switch_facts['network_services']['l3'] is True:
        switch_facts['virtual_router_mac_address'] = pod['commonMlagConfig']['virtualRouterMacAddress']

    # Define switch uplink info
    if switch_facts.get('uplink_interfaces'):
        switch_facts['uplink_switches_ids'] = [
            info['neighborId'] for info in switch_facts['uplink_interfaces'].values()
        ]
        switch_facts['uplink_switch_interfaces'] = [
            info['neighborInterface'] for info in switch_facts['uplink_interfaces'].values()
        ]
    else:
        switch_facts['uplink_switches_ids'] = []
        switch_facts['uplink_switch_interfaces'] = []

    # Define switch downlink neighbor ids
    if switch_facts.get('downlink_interfaces'):
        switch_facts['downlink_switches_ids'] = [
            info['neighborId'] for info in switch_facts['downlink_interfaces'].values()
        ]
    else:
        switch_facts['downlink_switches_ids'] = []

    # Get mlag settings
    if switch_facts.get('mlag_peer_link_interfaces'):
        switch_facts['mlag'] = False  # set the default setting to False
        if switch_facts.get('mlag_support') and len(switch_facts['mlag_peer_link_interfaces']) > 0:
            # Turn MLAG on/off depending on the setting at the l2/l3 leaf domain input
            if switch_facts['type'] == "l3leaf":
                switch_facts['mlag'] = l3_leaf_domain['l3LeafMlag']
            elif switch_facts['type'] == "l2leaf":
                switch_facts['mlag'] = l2_leaf_domain['l2LeafMlag']
            # If MLAG is on, set other switch_fact properties necessary to generate MLAG config
            if switch_facts['mlag'] is True:
                if switch_facts['type'] == "spine":
                    switch_facts['mlag_group'] = f"{switch_facts['pod']}_Spines"
                elif switch_facts['type'] == "l3leaf":
                    switch_facts['mlag_group'] = f"L3LeafDomain{switch_facts['l3_leaf_domain']}"
                elif switch_facts['type'] == "l2leaf":
                    switch_facts['mlag_group'] = f"L2LeafDomain{switch_facts['l2_leaf_domain']}"
                # Enable l3 mlag peering, for MLAG switches that are also routing
                if switch_facts['underlay_router'] is True:
                    switch_facts['mlag_l3'] = True
                else:
                    switch_facts['mlag_l3'] = False

                switch_facts['mlag_peer_vlan'] = pod['commonMlagConfig']['mlagVlan']
                # Set mlag_peer_l3_vlan if there is value set in studio input
                switch_facts['mlag_peer_l3_vlan'] = pod['commonMlagConfig']['mlagPeerL3Vlan'] \
                    if pod['commonMlagConfig']['mlagPeerL3Vlan'] is not None else switch_facts['mlag_peer_vlan']
                switch_facts['mlag_port_channel_id'] = pod['commonMlagConfig']['mlagPortChannelId']
                switch_facts['mlag_peer_ipv4_pool'] = pod['commonMlagConfig']['mlagPeerLinkSubnet']
                # Set mlag_peer_l3_ipv4_pool if mlag_peer_l3_vlan is different from mlag_peer_vlan
                if pod['commonMlagConfig']['mlagPeerL3Subnet'].strip() != "" \
                        and switch_facts['mlag_peer_vlan'] != switch_facts['mlag_peer_l3_vlan']:
                    switch_facts['mlag_peer_l3_ipv4_pool'] = pod['commonMlagConfig']['mlagPeerL3Subnet']
                else:
                    switch_facts['mlag_peer_l3_ipv4_pool'] = switch_facts['mlag_peer_ipv4_pool']
                switch_facts['mlag_subnet_mask'] = pod['commonMlagConfig']['mlagSubnetMask']
                # Set mlag_l3_subnet_mask if mlag_peer_l3_vlan is different from mlag_peer_vlan
                if pod['commonMlagConfig']['mlagPeerL3SubnetMask'] is not None \
                        and switch_facts['mlag_peer_vlan'] != switch_facts['mlag_peer_l3_vlan']:
                    switch_facts['mlag_l3_subnet_mask'] = pod['commonMlagConfig']['mlagPeerL3SubnetMask']
                else:
                    switch_facts['mlag_l3_subnet_mask'] = switch_facts['mlag_subnet_mask']
                switch_facts['mlag_lacp_mode'] = pod['commonMlagConfig']['lacpMode']
                switch_facts['reload_delay_mlag'] = switch_facts['platform_settings']['reload_delay']['mlag']
                switch_facts['reload_delay_non_mlag'] = switch_facts['platform_settings']['reload_delay']['non_mlag']
                switch_facts['mlag_ibgp_origin_incomplete'] = True
                switch_facts['mlag_peer_serial_number'] = [
                    info['neighborId'] for info in switch_facts['mlag_peer_link_interfaces'].values()
                ][0]
                mlag_peer_switch_facts = switches_in_my_data_center[switch_facts['mlag_peer_serial_number']]
                switch_facts['mlag_peer'] = mlag_peer_switch_facts['hostname']
                switch_facts['mlag_peer_switch_interfaces'] = [
                    info['neighborInterface'] for info in switch_facts['mlag_peer_link_interfaces'].values()
                ]
                switch_facts['mlag_interfaces'] = [
                    iface for iface in switch_facts['mlag_peer_link_interfaces'].keys()
                ]
                # If the NodeId of this switch is less than that of its MLAG peer, consider this switch the primary
                if int(switch_facts['id']) < int(mlag_peer_switch_facts['id']):
                    switch_facts['mlag_primary_id'] = int(switch_facts['id'])
                    switch_facts['mlag_role'] = "primary"
                    switch_facts['mlag_ip'] = str(get_mlag_ip(switch_facts, switch_facts['mlag_peer_ipv4_pool'],
                                                switch_facts['mlag_subnet_mask'], "primary"))
                    switch_facts['mlag_l3_ip'] = str(get_mlag_ip(switch_facts, switch_facts['mlag_peer_l3_ipv4_pool'],
                                                    switch_facts['mlag_l3_subnet_mask'], "primary"))
                    switch_facts['mlag_peer_ip'] = str(get_mlag_ip(switch_facts, switch_facts['mlag_peer_ipv4_pool'],
                                                    switch_facts['mlag_subnet_mask'], "secondary"))
                    switch_facts['mlag_peer_l3_ip'] = str(get_mlag_ip(switch_facts, switch_facts['mlag_peer_l3_ipv4_pool'],
                                                        switch_facts['mlag_l3_subnet_mask'], "secondary"))
                # If the NodeId of this switch is greater than that of its MLAG peer, consider this switch the secondary
                else:
                    switch_facts['mlag_primary_id'] = int(mlag_peer_switch_facts['id'])
                    switch_facts['mlag_role'] = "secondary"
                    switch_facts['mlag_ip'] = str(get_mlag_ip(switch_facts, switch_facts['mlag_peer_ipv4_pool'],
                                                switch_facts['mlag_subnet_mask'], "secondary"))
                    switch_facts['mlag_l3_ip'] = str(get_mlag_ip(switch_facts, switch_facts['mlag_peer_l3_ipv4_pool'],
                                                    switch_facts['mlag_l3_subnet_mask'], "secondary"))
                    switch_facts['mlag_peer_ip'] = str(get_mlag_ip(switch_facts, switch_facts['mlag_peer_ipv4_pool'],
                                                    switch_facts['mlag_subnet_mask'], "primary"))
                    switch_facts['mlag_peer_l3_ip'] = str(get_mlag_ip(switch_facts, switch_facts['mlag_peer_l3_ipv4_pool'],
                                                        switch_facts['mlag_l3_subnet_mask'], "primary"))

    # Set underlay routing details
    if switch_facts['underlay_router'] is True:
        # Parse user inputs to get transit ip pools
        if switch_facts['type'] == "spine":
            switch_facts['uplink_ipv4_pool'] = [pod['underlayRouting']['spineSuperSpineFabricSubnet']]
            switch_facts['uplink_ipv4_subnet_mask'] = int(pod['underlayRouting']['spineSuperSpineFabricSubnetMask']) \
                if pod['underlayRouting']['spineSuperSpineFabricSubnetMask'] is not None else None
            # Validate subnets
            overlapping_networks_check(switch_facts['uplink_ipv4_pool'])
        elif switch_facts['type'] == "l3leaf":
            switch_facts['uplink_ipv4_pool'] = [
                pool.strip() for pool in pod['underlayRouting']['underlayFabricSubnet'].split(",")
            ]
            switch_facts['uplink_ipv4_subnet_mask'] = int(pod['underlayRouting']['underlayFabricSubnetMask'])
            # Validate subnets
            overlapping_networks_check(switch_facts['uplink_ipv4_pool'])
            # if the spine uplink pool is the same as the leaf uplink pool, calculate the uplink offset for leafs
            if len(switch_facts['uplink_ipv4_pool']) == 1 \
                    and pod['underlayRouting'].get('spineSuperSpineFabricSubnet') \
                    and pod['underlayRouting']['spineSuperSpineFabricSubnet'].strip() \
                    == pod['underlayRouting']['underlayFabricSubnet'].strip():
                # offset is total number of links to super spines from all spines in this pod
                switch_facts['uplink_offset'] = switch_facts['max_spines'] * switch_facts['max_super_spines'] * \
                    switch_facts['max_parallel_uplinks_to_super_spines']
            # If there are multiple leaf uplink pools supplied, check that the spine uplink pool
            # is not also a pool in the leaf uplink pools
            else:
                # If a spine uplink subnet is present, check for no duplicate pool in
                # leaf uplink pools and spine uplink pools and set upplink offset to 0
                if pod['underlayRouting'].get('spineSuperSpineFabricSubnet'):
                    assert pod['underlayRouting']['spineSuperSpineFabricSubnet'].strip() \
                        not in switch_facts['uplink_ipv4_pool'], "When supplying multiple ipv4 subnets for " \
                        "leaf uplinks, you may not use one of those subnets for the spine uplink ipv4 subnet."
                switch_facts['uplink_offset'] = 0

        # Parse user inputs to get router ids
        if switch_facts['type'] == "spine":
            switch_facts['loopback_ipv4_pool'] = pod['commonBGPConfig']['spineLoopback0Subnet']
            switch_facts['loopback_ipv4_offset'] = 0
        elif switch_facts['type'] == "l3leaf":
            switch_facts['loopback_ipv4_pool'] = pod['commonBGPConfig']['leafLoopback0Subnet']
            # If leaf loopback pool is the same as spine loopback pool, calculate offset
            if pod['commonBGPConfig']['spineLoopback0Subnet'] == pod['commonBGPConfig']['leafLoopback0Subnet']:
                switch_facts['loopback_ipv4_offset'] = switch_facts['max_spines']
            else:
                # Offset is 0
                switch_facts['loopback_ipv4_offset'] = 0
        # Super-spine loopback pool must be different from pod loopback pools
        elif switch_facts['type'] == "super_spine":
            switch_facts['loopback_ipv4_pool'] = super_spine_plane['bgpConfiguration']['superSpineRouterIdSubnet']
            switch_facts['loopback_ipv4_offset'] = 0

        # Set Router ID
        switch_facts['router_id'] = str(get_router_id(switch_facts))

        # Set BGP parameters for spines, leafs, and super_spines
        # Checking for "" in the event of a super_spine
        if switch_facts['underlay_routing_protocol'].strip() in ["bgp", ""] or \
                switch_facts['overlay_routing_protocol'].strip() in ["bgp", ""]:
            if switch_facts['type'] == "spine":
                asns = string_to_list(str(pod['commonBGPConfig']['spineAsn']))
                if len(asns) > 1:
                    switch_facts['bgp_as'] = asns[switch_facts['id'] - 1]
                else:
                    switch_facts['bgp_as'] = asns[0]
                switch_facts['bgp_defaults'] = pod['commonBGPConfig']['spineBgpDefaults']
                switch_facts['bgp_maximum_paths'] = switch_facts['max_super_spines'] * \
                    switch_facts['max_parallel_uplinks_to_super_spines'] if \
                    switch_facts['max_super_spines'] * switch_facts['max_parallel_uplinks_to_super_spines'] > \
                    2 * switch_facts['max_parallel_uplinks_to_spines'] \
                    else 2 * switch_facts['max_parallel_uplinks_to_spines']
                switch_facts['bgp_ecmp'] = switch_facts['max_super_spines'] * \
                    switch_facts['max_parallel_uplinks_to_super_spines'] if \
                    switch_facts['max_super_spines'] * switch_facts['max_parallel_uplinks_to_super_spines'] > \
                    2 * switch_facts['max_parallel_uplinks_to_spines'] \
                    else 2 * switch_facts['max_parallel_uplinks_to_spines']
                # Set BGP peering
                switch_facts['dynamic_bgp_ipv4_peering'] = pod['commonBGPConfig']['spineBGPDynamicNeighbors']
                switch_facts['dynamic_bgp_evpn_peering'] = switch_facts['dynamic_bgp_ipv4_peering']
            elif switch_facts['type'] == "l3leaf":
                if switch_facts.get('bgp_as') is None:
                    range_asns = leaf_asns[switch_facts['pod']]
                    manual_asns = manually_set_leaf_domains[switch_facts['pod']]
                    if len(range_asns) > 1:
                        # get leaf_domain shift
                        leaf_domain_shift = len([ld for ld in manual_asns.keys()
                                                if ld < int(switch_facts['group_index'])])
                        switch_facts['leaf_domain_shift'] = leaf_domain_shift
                        # set bgp_as
                        switch_facts['bgp_as'] = range_asns[int(switch_facts['group_index']) - leaf_domain_shift]
                    else:
                        switch_facts['bgp_as'] = range_asns[0]
                switch_facts['bgp_defaults'] = pod['commonBGPConfig']['leafBgpDefaults']
                switch_facts['bgp_maximum_paths'] = switch_facts['max_spines'] * switch_facts['max_parallel_uplinks']
                switch_facts['bgp_ecmp'] = switch_facts['max_spines'] * switch_facts['max_parallel_uplinks']
                switch_facts['dynamic_bgp_ipv4_peering'] = False
                switch_facts['dynamic_bgp_evpn_peering'] = False
            elif switch_facts['type'] == "super_spine":
                switch_facts['bgp_as'] = super_spine_plane['bgpConfiguration']['bgpAsn']
                switch_facts['bgp_defaults'] = super_spine_plane['bgpConfiguration']['superSpineBgpDefaults']
                switch_facts['dynamic_bgp_ipv4_peering'] = super_spine_plane['bgpConfiguration']['ipv4BgpDynamicPeering']
                switch_facts['dynamic_bgp_evpn_peering'] = super_spine_plane['bgpConfiguration']['evpnBgpDynamicPeering']

        # Set ospf parameters for spines and leafs
        if switch_facts['type'] in ["l3leaf", "spine"] and switch_facts['underlay_routing_protocol'] == "ospf":
            switch_facts['underlay_ospf_process_id'] = pod['ospfConfiguration']['processId']
            switch_facts['underlay_ospf_area'] = pod['ospfConfiguration']['area']
            switch_facts['underlay_ospf_max_lsa'] = pod['ospfConfiguration']['maxLsa']
            switch_facts['underlay_ospf_bfd_enable'] = pod['ospfConfiguration']['bfd']
            if switch_facts['type'] == "l3leaf":
                switch_facts['ospf_defaults'] = pod['ospfConfiguration']['leafOspfDefaults']
            elif switch_facts['type'] == "spine":
                switch_facts['ospf_defaults'] = pod['ospfConfiguration']['spineOspfDefaults']

        # Set VTEP details for leaf and spines
        if pod is not None and pod['overlayDetails']['vxlanOverlay'] is True:
            if switch_facts['type'] == "spine":
                if len(switch_facts['uplink_interfaces']) > 0:
                    # Assume super spines will be evpn servers and clear evpn server setting on spines
                    switch_facts['evpn_role'] = None
            elif switch_facts['type'] == "l3leaf":
                if switch_facts.get('vtep'):
                    switch_facts['vtep_loopback_ipv4_pool'] = pod['overlayDetails']['leafLoopback1Subnet']
                    switch_facts['vtep_loopback'] = "Loopback1"
                    switch_facts['vtep_ip'] = str(get_vtep_loopback(switch_facts))
                    switch_facts['vtep_vvtep_ip'] = pod['overlayDetails']['vVtepAddress']
        if pod is not None and pod['overlayDetails']['vxlanOverlay'] is False:
            # Turn off the vtep setting since there are no VTEPs in this network
            switch_facts['vtep'] = False
            # Clear evpn role
            switch_facts['evpn_role'] = None
    
        #Ahmad get Underlay Multicast
        switch_facts["underlay_multicast"] = pod['underlayRouting']['underlayMulticast']

        #Ahmad get Overlay Multicast
        switch_facts["evpn_multicast"] = pod['commonBGPConfig']['evpnMulticast']

        #Ahmad get RT Membership
        switch_facts["evpn_rt_membership"] = pod['commonBGPConfig']['evpnRtMembership']


    


    if re.match(veos_regex, switch_facts['platform']):
        switch_facts['p2p_uplinks_mtu'] = 1500
    else:
        switch_facts['p2p_uplinks_mtu'] = 9214

    return switch_facts


def clear_evpn_role(switch_facts, data_center_resolver):
    '''
    Used to clear the EVPN roles in non VXLAN topologies and on spines in
    super-spine topologies.
    '''
    # Process Studio inputs
    try:
        data_center = data_center_resolver.resolve(device=switch_facts['serial_number'])['dataCenter']
        pod = data_center['pods'].resolve(device=switch_facts['serial_number'])['pod']
    except Exception:
        # Just in case something doesn't resolve, return switch_facts unmodified
        return switch_facts
    # Return if pod is none (which is the case when super-spines resolve a pod to None)
    if pod is None:
        return switch_facts
    # Clear EVPN roles if necessary
    if pod['overlayDetails']['vxlanOverlay'] is True:
        if switch_facts['type'] == "spine" and len(switch_facts['uplink_interfaces']) > 0:
            switch_facts['evpn_role'] = None
    else:
        # clear evpn role
        switch_facts['evpn_role'] = None
    return switch_facts


def set_base_config(config, switch_facts):
    # Set spanning tree
    if switch_facts.get('spanning_tree_mode'):
        config['spanning_tree']['mode'] = switch_facts['spanning_tree_mode']
    # Set tcam profile
    if switch_facts['platform_settings'].get('tcam_profile'):
        config['tcam_profile'] = {
            "system": switch_facts['platform_settings']['tcam_profile']
        }
    # Set routing
    #Ahmad adding EVPN Multicast Logic
    config['service_routing_protocols_model'] = "multi-agent"
    if switch_facts['underlay_router'] is True:
        config['ip_routing'] = True
        if switch_facts.get('underlay_multicast'):
            if switch_facts.get('evpn_multicast'):
                config['router_multicast'] = {
                    "ipv4": {
                        "routing": True,
                        "software_forwarding": "sfe"
                    }
                }
            else:
                config['router_multicast'] = {
                    "ipv4": {
                        "routing": True
                    }
                }

    # Set router-bgp
    if switch_facts['underlay_router'] is True \
            and (switch_facts['underlay_routing_protocol'] == "bgp"
                or switch_facts['overlay_routing_protocol'] == "bgp"):
        config['router_bgp']['as'] = switch_facts['bgp_as']
        config['router_bgp']['router_id'] = switch_facts['router_id']
        config['router_bgp']['bgp_defaults'] = switch_facts['bgp_defaults']
        if switch_facts.get('bgp_maximum_paths'):
            config['router_bgp']['maximum_paths'] = switch_facts['bgp_maximum_paths']
        if switch_facts.get('bgp_ecmp'):
            config["router_bgp"]['ecmp'] = switch_facts['bgp_ecmp']

    # Set platform_settings eos_cli
    if switch_facts['platform_settings'].get('eos_cli'):
        config['eos_cli'] = switch_facts['platform_settings']['eos_cli']

    #Ahmad Trident Forwarding table Partition if configured
    if switch_facts['platform_settings'].get('trident_forwarding_table_partition') is not None and switch_facts['evpn_multicast']:
        config["platform_settings"] = {
                "trident": {
                    "forwarding_table_partition": switch_facts['platform_settings']['trident_forwarding_table_partition']
                }
            }
        

    return config


def set_mlag_config(config, switch_facts):
    if switch_facts.get('mlag'):
        # Set spanning tree relevant config
        if switch_facts['mlag_l3'] is True and switch_facts['mlag_peer_l3_vlan'] != switch_facts['mlag_peer_vlan']:
            config['spanning_tree']['no_spanning_tree_vlan'] = ",".join(
                [str(switch_facts['mlag_peer_l3_vlan']), str(switch_facts['mlag_peer_vlan'])]
            )
        else:
            config['spanning_tree']['no_spanning_tree_vlan'] = switch_facts['mlag_peer_vlan']

        # Set mlag vlan
        if switch_facts['mlag_l3'] is True and switch_facts['mlag_peer_l3_vlan'] != switch_facts['mlag_peer_vlan']:
            config['vlans'][switch_facts['mlag_peer_l3_vlan']] = {
                "tenant": "system",
                "name": "LEAF_PEER_L3",
                "trunk_groups": ['LEAF_PEER_L3']
            }
        config['vlans'][switch_facts['mlag_peer_vlan']] = {
            "tenant": "system",
            "name": "MLAG_PEER",
            "trunk_groups": ['MLAG']
        }

        # Set mlag svi
        if switch_facts['mlag_l3'] is True and switch_facts['mlag_peer_l3_vlan'] != switch_facts['mlag_peer_vlan']:
            config['vlan_interfaces'][f"Vlan{switch_facts['mlag_peer_l3_vlan']}"] = {
                "description": "MLAG_PEER_L3_PEERING",
                "shutdown": False,
                "ip_address": f"{switch_facts['mlag_l3_ip']}/{switch_facts['mlag_l3_subnet_mask']}",
                "no_autostate": True,
                "mtu": switch_facts['p2p_uplinks_mtu']
            }
        config['vlan_interfaces'][f"Vlan{switch_facts['mlag_peer_vlan']}"] = {
            "description": "MLAG_PEER",
            "shutdown": False,
            "ip_address": f"{switch_facts['mlag_ip']}/{switch_facts['mlag_subnet_mask']}",
            "no_autostate": True,
            "mtu": switch_facts['p2p_uplinks_mtu']
        }
        if switch_facts['mlag_l3'] is True and switch_facts['underlay_routing_protocol'] == "ospf":
            config['vlan_interfaces'][f"Vlan{switch_facts['mlag_peer_l3_vlan']}"]['ospf_network_point_to_point'] = True
            config['vlan_interfaces'][f"Vlan{switch_facts['mlag_peer_l3_vlan']}"]['ospf_area'] \
                = switch_facts['underlay_ospf_area']
            
        #Ahmad SVI mlag multicast config, enable sparse mode and configure local-interface to be ping test loopback if evpn_multiast is set
        if switch_facts['mlag_l3'] is True and switch_facts['underlay_multicast']:
            config['vlan_interfaces'][f"Vlan{switch_facts['mlag_peer_l3_vlan']}"]["pim"] = {"ipv4": {"sparse_mode": True}}

        # Set port-channel interfaces
        mlag_peer = switch_facts['mlag_peer']  # Friendlier variable name for creating custom interface description
        mlag_port_channel_id = switch_facts['mlag_port_channel_id']  # Friendlier variable name for creating custom interface description
        config['port_channel_interfaces'][f"Port-Channel{switch_facts['mlag_port_channel_id']}"] = {
            "description": eval(f"f\"{fabric_variables['interface_descriptions']['mlag_port_channel_interface']}\""),
            "type": "switched",
            "shutdown": False,
            "mode": "trunk",
            "trunk_groups": ['MLAG']
        }
        if switch_facts['mlag_l3'] is True and switch_facts['mlag_peer_l3_vlan'] != switch_facts['mlag_peer_vlan']:
            config['port_channel_interfaces'][f"Port-Channel{switch_facts['mlag_port_channel_id']}"]['trunk_groups']\
                .append("LEAF_PEER_L3")

        # Set ethernet interfaces
        for i, iface in enumerate(switch_facts['mlag_interfaces']):
            mlag_peer = switch_facts['mlag_peer']
            mlag_peer_interface = switch_facts['mlag_peer_switch_interfaces'][i]
            config['ethernet_interfaces'][iface] = {
                "peer": mlag_peer,
                "peer_interface": mlag_peer_interface,
                "peer_type": "mlag",
                "description": eval(f"f\"{fabric_variables['interface_descriptions']['mlag_ethernet_interfaces']}\""),
                "type": "switched",
                "shutdown": False,
                "channel_group": {
                    "id": switch_facts['mlag_port_channel_id'],
                    "mode": switch_facts['mlag_lacp_mode']
                }
            }

        # Set mlag config
        config['mlag_configuration'] = {
            "enabled": True,
            "domain_id": switch_facts['mlag_group'],
            "local_interface": f"Vlan{switch_facts['mlag_peer_vlan']}",
            "peer_address": switch_facts['mlag_peer_ip'],
            "peer_link": f"Port-Channel{switch_facts['mlag_port_channel_id']}",
            "reload_delay_mlag": switch_facts['reload_delay_mlag'],
            "reload_delay_non_mlag": switch_facts['reload_delay_non_mlag']
        }

        # Set route maps
        # Origin Incomplete for MLAG iBGP learned routes
        if switch_facts['mlag_l3'] is True and \
                switch_facts['mlag_ibgp_origin_incomplete'] is True and \
                switch_facts['underlay_routing_protocol'] == "bgp":
            config['route_maps']["RM-MLAG-PEER-IN"] = {
                "sequence_numbers": {
                    10: {
                        "type": "permit",
                        "set": ["origin incomplete"],
                        "description": "Make routes learned over MLAG Peer-link less "
                                    "preferred on spines to ensure optimal routing"
                    }
                }
            }

        # Set bgp config
        if switch_facts['mlag_l3'] is True and switch_facts['underlay_routing_protocol'] == "bgp":
            (config['router_bgp']['peer_groups']
            [fabric_variables['bgp_peer_groups']['MLAG_IPv4_UNDERLAY_PEER']['name']]) = {
                "type": "ipv4",
                "remote_as": switch_facts['bgp_as'],
                "next_hop_self": True,
                "maximum_routes": 12000,
                "send_community": "all"
            }
            if fabric_variables['bgp_peer_groups']['MLAG_IPv4_UNDERLAY_PEER']['password'] is not None:
                (config['router_bgp']['peer_groups']
                [fabric_variables['bgp_peer_groups']['MLAG_IPv4_UNDERLAY_PEER']['name']]['password']) = \
                    fabric_variables['bgp_peer_groups']['MLAG_IPv4_UNDERLAY_PEER']['password']
            if switch_facts['mlag_ibgp_origin_incomplete'] is True:
                (config['router_bgp']['peer_groups']
                [fabric_variables['bgp_peer_groups']['MLAG_IPv4_UNDERLAY_PEER']['name']]['route_map_in']) = \
                    "RM-MLAG-PEER-IN"
            (config['router_bgp']['address_family_ipv4']['peer_groups']
            [fabric_variables['bgp_peer_groups']['MLAG_IPv4_UNDERLAY_PEER']['name']]) = {
                "activate": True
            }
            config['router_bgp']['neighbor_interfaces'][f"Vlan{switch_facts['mlag_peer_l3_vlan']}"] = {
                "peer_group": fabric_variables['bgp_peer_groups']['MLAG_IPv4_UNDERLAY_PEER']['name'],
                "remote_as": switch_facts['bgp_as'],
                "description": switch_facts['mlag_peer']
            }
            config['router_bgp']['neighbors'][switch_facts['mlag_peer_l3_ip']] = {
                "peer_group": fabric_variables['bgp_peer_groups']['MLAG_IPv4_UNDERLAY_PEER']['name'],
                "description": switch_facts['mlag_peer']
            }

    return config


def set_underlay_config(config, switch_facts):
    underlay_data = {}
    underlay_data['links'] = switch_facts['topology']['links']
    # First add interface details from devices whose uplink interface neighbors are this switch
    for sn in switch_facts['downlink_switches_ids']:
        neighbor_switch_facts = switches_in_my_data_center[sn]
        for neighbor_link, neighbor_link_info in neighbor_switch_facts['topology']['links'].items():
            if neighbor_link_info['peer_id'] == switch_facts['serial_number']:
                link_facts = {}
                link_facts['peer_id'] = neighbor_switch_facts['serial_number']
                link_facts['peer'] = neighbor_switch_facts['hostname']
                link_facts['peer_interface'] = neighbor_link
                link_facts['peer_type'] = neighbor_switch_facts['type']
                link_facts['peer_bgp_as'] = neighbor_switch_facts.get('bgp_as')
                link_facts['type'] = neighbor_link_info['type']
                link_facts['ip_address'] = neighbor_link_info.get('peer_ip_address')
                link_facts['peer_ip_address'] = neighbor_link_info.get('ip_address')
                link_facts['subnet_mask'] = neighbor_link_info.get('subnet_mask')
                link_facts['channel_group_id'] = neighbor_link_info.get('peer_channel_group_id')
                link_facts['peer_channel_group_id'] = neighbor_link_info.get('channel_group_id')
                link_facts['channel_description'] = neighbor_link_info.get('peer_channel_description')
                interface = neighbor_link_info['peer_interface']
                underlay_data['links'][interface] = link_facts
                #Ahmad multicast/pim
                link_facts["underlay_multicast"] = neighbor_link_info.get("underlay_multicast")
                #link_facts['pim_enabled'] = neighbor_link_info.get('pim_enabled')
                

    # Set Ethernet interfaces
    for iface in underlay_data['links']:
        link = underlay_data['links'][iface]  # Friendlier variable name for creating custom interface description
        if link['type'] == "underlay_p2p":
            config['ethernet_interfaces'][iface] = {
                "peer": link['peer'],
                "peer_interface": link['peer_interface'],
                "peer_type": link['peer_type'],
                "description": eval(f"f\"{fabric_variables['interface_descriptions']['underlay_l3_ethernet_interfaces']}\""),
                "mtu": switch_facts['p2p_uplinks_mtu'],
                "type": "routed",
                "shutdown": False,
                "ip_address": f"{link['ip_address']}/{link['subnet_mask']}"
            }
            if switch_facts['underlay_routing_protocol'] == "ospf":
                config['ethernet_interfaces'][iface]['ospf_network_point_to_point'] = True
                config['ethernet_interfaces'][iface]['ospf_area'] = switch_facts['underlay_ospf_area']
            #Ahmad PIM Config on Interface
            #if link.get('pim_enabled'):
            if link.get('underlay_multicast'):
                config['ethernet_interfaces'][iface]['pim'] = {
                    "ipv4": {"sparse_mode": True}
                }
            if len(fabric_variables['p2p_interface_settings']) > 0:
                config['ethernet_interfaces'][iface]['defaults'] = fabric_variables['p2p_interface_settings']

        elif link['type'] == "underlay_l2":
            config['ethernet_interfaces'][iface] = {
                "peer": link['peer'],
                "peer_interface": link['peer_interface'],
                "peer_type": link['peer_type'],
                "description": eval(f"f\"{fabric_variables['interface_descriptions']['underlay_l2_ethernet_interfaces']}\""),
                "type": "routed",
                "shutdown": False
            }
            if link.get('channel_group_id'):
                config['ethernet_interfaces'][iface]['channel_group'] = {
                    "id": link['channel_group_id'],
                    "mode": "active"
                }

    # Set Port-Channel interfaces
    port_channel_list = []
    for iface in underlay_data['links']:
        link = underlay_data['links'][iface]
        if link['type'] == "underlay_l2" \
                and link.get('channel_group_id') \
                and link.get('channel_group_id') not in port_channel_list:
            port_channel_list.append(link['channel_group_id'])
            config['port_channel_interfaces'][f"Port-Channel{link['channel_group_id']}"] = {
                "description": eval(f"f\"{fabric_variables['interface_descriptions']['underlay_port_channel_interfaces']}\""),
                "type": "switched",
                "shutdown": False,
                "mode": "trunk",
                "mlag": link['channel_group_id']
            }
    # L2 and L3
    if switch_facts['network_services']['l2'] is True \
            and switch_facts['network_services']['l3'] is True:
        # set viritual router mac address
        config['ip_virtual_router_mac_address'] = switch_facts['virtual_router_mac_address']

    # Routing
    if switch_facts['underlay_router'] is True:
        # Set loopback interfaces
        if switch_facts.get('router_id'):
            config['loopback_interfaces']['Loopback0'] = {
                "description": eval(f"f\"{fabric_variables['interface_descriptions']['router_id_interface']}\""),
                "shutdown": False,
                "ip_address": f"{switch_facts['router_id']}/32",
            }
            if switch_facts['underlay_routing_protocol'] == "ospf":
                config['loopback_interfaces']['Loopback0']['ospf_area'] = switch_facts['underlay_ospf_area']
        if switch_facts['vtep'] is True:
            config['loopback_interfaces'][switch_facts['vtep_loopback']] = {
                "description": eval(f"f\"{fabric_variables['interface_descriptions']['vtep_source_interface']}\""),
                "shutdown": False,
                "ip_address": f"{switch_facts['vtep_ip']}/32"
            }
            if switch_facts.get('vtep_vvtep_ip') and switch_facts['network_services'].get('l3'):
                config['loopback_interfaces'][switch_facts['vtep_loopback']]['ip_address_secondaries'] \
                    = [switch_facts['vtep_vvtep_ip']]
            if switch_facts['underlay_routing_protocol'] == "ospf":
                config['loopback_interfaces'][switch_facts['vtep_loopback']]['ospf_area'] \
                    = switch_facts['underlay_ospf_area']

        # Set bgp if necessary
        if switch_facts['underlay_routing_protocol'] == "bgp":
            config['router_bgp']['peer_groups'][fabric_variables['bgp_peer_groups']['IPv4_UNDERLAY_PEERS']['name']] = {
                "type": "ipv4",
                "maximum_routes": 12000,
                "send_community": "all"
            }
            if fabric_variables['bgp_peer_groups']['IPv4_UNDERLAY_PEERS']['password'] is not None:
                (config['router_bgp']['peer_groups']
                [fabric_variables['bgp_peer_groups']['IPv4_UNDERLAY_PEERS']['name']]['password']) \
                    = fabric_variables['bgp_peer_groups']['IPv4_UNDERLAY_PEERS']['password']
            (config['router_bgp']['address_family_ipv4']['peer_groups']
            [fabric_variables['bgp_peer_groups']['IPv4_UNDERLAY_PEERS']['name']]) = {
                "activate": True,
            }
            config['router_bgp']['redistribute_routes']['connected'] = {
                "route_map": "RM-CONN-2-BGP"
            }
            for iface, link in underlay_data['links'].items():
                if link['type'] == "underlay_p2p":
                    config['router_bgp']['neighbors'][link['peer_ip_address']] = {
                        "peer_group": fabric_variables['bgp_peer_groups']['IPv4_UNDERLAY_PEERS']['name'],
                        "remote_as": link['peer_bgp_as'],
                        "description": f"{link['peer']}_{link['peer_interface']}"
                    }
            # Create peer filter and listen range statements if necessary
            if switch_facts.get('dynamic_bgp_ipv4_peering'):
                # Divide remote_asns into as few continuous asn ranges as possible
                dynamic_peering_peer_filter_statements = []
                from operator import itemgetter
                from itertools import groupby
                remote_asns = list(
                    set(
                        [
                            int(link['peer_bgp_as']) for link in underlay_data['links'].values()
                            if link['type'] == "underlay_p2p"
                            and switches_in_my_data_center[link['peer_id']].get('uplink_ipv4_pool')
                        ]
                    )
                )
                if len(remote_asns) > 1:
                    for k, g in groupby(enumerate(remote_asns), lambda x: x[0]-x[1]):
                        group = map(itemgetter(1), g)
                        group = list(map(int, group))
                        dynamic_peering_peer_filter_statements.append(f"{group[0]}-{group[-1]}")
                else:
                    dynamic_peering_peer_filter_statements = remote_asns

                # Define Peer Filer
                config['peer_filters']["DOWNLINK-IPv4-NEIGHBORS"] = {
                    "sequence_numbers": {}
                }
                # Populate peer filter
                for i, statement in enumerate(dynamic_peering_peer_filter_statements):
                    config['peer_filters']["DOWNLINK-IPv4-NEIGHBORS"]['sequence_numbers'][(i+1)*10] = {
                        "match": f"as-range {statement} result accept"
                    }
                # Define BGP Listen Range neighbor statement
                # Get downlink neighbor uplink pools
                neighbor_uplink_pools = []
                for link in underlay_data['links'].values():
                    if switches_in_my_data_center[link['peer_id']].get('uplink_ipv4_pool'):
                        for pool in switches_in_my_data_center[link['peer_id']]['uplink_ipv4_pool']:
                            neighbor_uplink_pools.append(pool)
                        # remove remove remote_as statement for neighbor
                        del(config['router_bgp']['neighbors'][link['peer_ip_address']])
                # initialize bgp_listen_range_prefixes
                (config['router_bgp']['peer_groups']
                [fabric_variables['bgp_peer_groups']['IPv4_UNDERLAY_PEERS']['name']]
                ['bgp_listen_range_prefixes']) = {}
                # populate bgp_listen_range_prefixes
                for uplink_pool in list(set(neighbor_uplink_pools)):
                    (config['router_bgp']['peer_groups'][fabric_variables['bgp_peer_groups']['IPv4_UNDERLAY_PEERS']['name']]
                    ['bgp_listen_range_prefixes'][uplink_pool]) = {
                        "peer_filter": "DOWNLINK-IPv4-NEIGHBORS"
                    }
            # Create prefix lists
            config['prefix_lists']["PL-LOOPBACKS-EVPN-OVERLAY"] = {
                "sequence_numbers": {
                    10: {
                        "action": f"permit {switch_facts['loopback_ipv4_pool']} eq 32"
                    }
                }
            }
            if switch_facts.get('vtep_ip') is not None:
                config['prefix_lists']["PL-LOOPBACKS-EVPN-OVERLAY"]['sequence_numbers'][20] = {
                    "action": f"permit {switch_facts['vtep_loopback_ipv4_pool']} eq 32"
                }
            if switch_facts.get('vtep_vvtep_ip') is not None \
                    and switch_facts.get('evpn_services_l2_only') is not None \
                    and switch_facts.get('evpn_services_l2_only') is False:
                config['prefix_lists']["PL-LOOPBACKS-EVPN-OVERLAY"]['sequence_numbers'][30] = {
                    "action": f"permit {switch_facts['vtep_vvtep_ip']}"
                }
            # Create route-maps
            config['route_maps']["RM-CONN-2-BGP"] = {
                "sequence_numbers": {
                    10: {
                        "type": "permit",
                        "match": ["ip address prefix-list PL-LOOPBACKS-EVPN-OVERLAY"]
                    }
                }
            }
        if switch_facts['underlay_routing_protocol'] == "ospf":
            config['router_ospf']['process_ids'] = {
                switch_facts['underlay_ospf_process_id']: {
                    "passive_interface_default": True,
                    "router_id": switch_facts['router_id'],
                    "no_passive_interfaces": [],
                    "max_lsa": switch_facts['underlay_ospf_max_lsa'],
                    "ospf_defaults": switch_facts.get('ospf_defaults')
                }
            }
            for iface, link in underlay_data['links'].items():
                if link['type'] == "underlay_p2p":
                    (config['router_ospf']['process_ids'][switch_facts['underlay_ospf_process_id']]
                    ['no_passive_interfaces']).append(iface)
            if switch_facts.get('mlag_l3') is not None and switch_facts.get('mlag_l3') is True:
                (config['router_ospf']['process_ids'][switch_facts['underlay_ospf_process_id']]
                ['no_passive_interfaces']).append(f"Vlan{switch_facts['mlag_peer_l3_vlan']}")
            if switch_facts['underlay_ospf_bfd_enable'] is True:
                config['bfd_enable'] = True

    return config


def set_overlay_config(config, switch_facts):
    if not switch_facts.get('underlay_router'):
        return config
    if switch_facts.get('overlay_routing_protocol') and switch_facts['overlay_routing_protocol'] != "bgp":
        return config

    overlay_data = {}
    # Set evpn route servers
    overlay_data['evpn_route_servers'] = {}
    if switch_facts.get('evpn_route_server_ids'):
        for rs_id in switch_facts['evpn_route_server_ids']:
            rs_switch_facts = switches_in_my_data_center[rs_id]
            if rs_switch_facts['evpn_role'] == "server":
                server = {
                    "bgp_as": rs_switch_facts['bgp_as'],
                    "ip_address": rs_switch_facts['router_id']
                }
                overlay_data['evpn_route_servers'][rs_switch_facts['hostname']] = server

    # Set evpn route clients
    overlay_data['evpn_route_clients'] = {}
    if switch_facts['evpn_role'] == "server":
        for data_center_switch_facts in switches_in_my_data_center.values():
            if data_center_switch_facts['evpn_role'] is not None and data_center_switch_facts['evpn_role'] == "client":
                if switch_facts['serial_number'] in data_center_switch_facts['evpn_route_server_ids']:
                    client = {
                        "bgp_as": data_center_switch_facts['bgp_as'],
                        "ip_address": data_center_switch_facts['router_id'],
                        "serial_number": data_center_switch_facts['serial_number']
                    }
                    overlay_data['evpn_route_clients'][data_center_switch_facts['hostname']] = client

    # Set ebgp
    if switch_facts.get('evpn_role'):
        config['router_bgp']['peer_groups'][fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name']] = {
            "type": "evpn",
            "update_source": "Loopback0",
            "bfd": True,
            "ebgp_multihop": str(fabric_variables['evpn_ebgp_multihop']),
            "send_community": "all",
            "maximum_routes": 0,
            "maximum_routes_warning_limit": 12000
        }
        if switch_facts['evpn_role'] == "server":
            (config['router_bgp']['peer_groups'][fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name']]
            ['next_hop_unchanged']) = True
        if fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['password'] is not None:
            (config['router_bgp']['peer_groups'][fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name']]
            ['password']) = fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['password']
        (config['router_bgp']['address_family_ipv4']['peer_groups']
        [fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name']]) = {
                "activate": False
            }
        (config['router_bgp']['address_family_evpn']['peer_groups']
        [fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name']]) = {
            "activate": True
        }
            
        if switch_facts.get('vtep_ip') and fabric_variables['evpn_hostflap_detection']['enabled'] is True:
            config['router_bgp']['address_family_evpn']['evpn_hostflap_detection'] = {
                "window": fabric_variables['evpn_hostflap_detection']['window'],
                "threshold": fabric_variables['evpn_hostflap_detection']['threshold'],
                "enabled": fabric_variables['evpn_hostflap_detection']['enabled']
            }
        
        #Ahmad rt-membership configurtion
        if switch_facts.get('evpn_rt_membership'):
            if switch_facts['evpn_role'] == "server":
                config['router_bgp']['address_family_rt']['peer_groups'] = {
                    f"{fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name']}": {
                        "activate": True,
                        "default_route_target": {
                        "only": True
                        }
                    }
                }
            else:
                config['router_bgp']['address_family_rt']['peer_groups'] = {
                    f"{fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name']}": {
                        "activate": True
                        }
                }
        
        # Overlay network peering
        for rs, info in overlay_data['evpn_route_servers'].items():
            config['router_bgp']['neighbors'][info['ip_address']] = {
                "peer_group": fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name'],
                "description": rs,
                "remote_as": info['bgp_as']
            }
            
        for cs, info in overlay_data['evpn_route_clients'].items():
            config['router_bgp']['neighbors'][info['ip_address']] = {
                "peer_group": fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name'],
                "description": cs,
                "remote_as": info['bgp_as']
            }

        # Create peer filter and listen range statements if necessary
        if switch_facts.get('dynamic_bgp_evpn_peering'):
            # Divide remote_asns into as few continuous asn ranges as possible
            dynamic_peering_peer_filter_statements = []
            from operator import itemgetter
            from itertools import groupby
            remote_asns = list(set([int(info['bgp_as']) for info in overlay_data['evpn_route_clients'].values()]))
            if len(remote_asns) > 1:
                for k, g in groupby(enumerate(remote_asns), lambda x: x[0]-x[1]):
                    group = map(itemgetter(1), g)
                    group = list(map(int, group))
                    dynamic_peering_peer_filter_statements.append(f"{group[0]}-{group[-1]}")
            else:
                dynamic_peering_peer_filter_statements = remote_asns

            # Define Peer Filer
            config['peer_filters']["DOWNLINK-EVPN-NEIGHBORS"] = {
                "sequence_numbers": {}
            }
            # Populate peer filter
            for i, statement in enumerate(dynamic_peering_peer_filter_statements):
                config['peer_filters']["DOWNLINK-EVPN-NEIGHBORS"]['sequence_numbers'][(i+1)*10] = {
                    "match": f"as-range {statement} result accept"
                }
            # Define BGP Listen Range neighbor statement
            # Get downlink neighbor uplink pools
            neighbor_loopback_pools = []
            for info in overlay_data['evpn_route_clients'].values():
                if switches_in_my_data_center[info['serial_number']].get('loopback_ipv4_pool'):
                    neighbor_loopback_pools.append(switches_in_my_data_center[info['serial_number']]['loopback_ipv4_pool'])
                    # remove remove remote_as statement for neighbor
                    del(config['router_bgp']['neighbors'][info['ip_address']])
            # initialize bgp_listen_range_prefixes
            (config['router_bgp']['peer_groups'][fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name']]
            ['bgp_listen_range_prefixes']) = {}
            # populate bgp_listen_range_prefixes
            for loopback_pool in list(set(neighbor_loopback_pools)):
                (config['router_bgp']['peer_groups'][fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name']]
                ['bgp_listen_range_prefixes'][loopback_pool]) = {
                    "peer_filter": "DOWNLINK-EVPN-NEIGHBORS"
                }
            #Ahmad Activate RT membership if enabled 
            #if switch_facts.get('evpn_rt_membership'):
            #    if switch_facts['evpn_role'] == "server":
            #        config['router_bgp']['address_family_rt']['peer_groups'] = {
            #        "name": fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name'],
            #        "activate": True,
            #        "default_route_target": {
            #        "only": True
            #            }
            #        }
            #    else:
            #        (config['router_bgp']['address_family_rt']['peer_groups']
            #        [fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name']]) = {
            #        "activate": True
            #        }
 
    return config


#Ahmad changes to vxlan definition to accomodate evpn_multicast
def set_vxlan_config(config, switch_facts):
    if switch_facts.get('vtep') is True:
        if switch_facts.get('mlag') and switch_facts.get('evpn_multicast'):
            config['vxlan_interface'] = {
                "Vxlan1": {
                    "description": f"{switch_facts['hostname']}_VTEP",
                    "vxlan": {
                        "source_interface": "Loopback0",
                        "udp_port": 4789,
                        "mlag_source_interface":  switch_facts['vtep_loopback']
                    }
                }
            }
        else:
            config['vxlan_interface'] = {
                "Vxlan1": {
                    "description": f"{switch_facts['hostname']}_VTEP",
                    "vxlan": {
                        "source_interface": switch_facts['vtep_loopback'],
                        "udp_port": 4789
                    }
                }
            }
        if switch_facts.get('mlag'):
            config['vxlan_interface']['Vxlan1']['vxlan']['virtual_router_encapsulation_mac_address'] = "mlag-system-id"
    return config


def set_fabric_variables(data_center_resolver):
    '''
    Updates global fabric_variables variable with custom settings
    '''
    data_center = data_center_resolver.resolve()['dataCenter']
    custom_fabric_variables = data_center['fabricSettings']
    # Update BGP variables
    if custom_fabric_variables['bgpPeerGroupSettings']['ipv4UnderlayPeerGroup']['name']:
        fabric_variables['bgp_peer_groups']['IPv4_UNDERLAY_PEERS']['name'] \
            = custom_fabric_variables['bgpPeerGroupSettings']['ipv4UnderlayPeerGroup']['name']

    if custom_fabric_variables['bgpPeerGroupSettings']['ipv4UnderlayPeerGroup']['password']:
        fabric_variables['bgp_peer_groups']['IPv4_UNDERLAY_PEERS']['password'] \
            = custom_fabric_variables['bgpPeerGroupSettings']['ipv4UnderlayPeerGroup']['password']

    if custom_fabric_variables['bgpPeerGroupSettings']['evpnOverlayPeerGroup']['name']:
        fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['name'] \
            = custom_fabric_variables['bgpPeerGroupSettings']['evpnOverlayPeerGroup']['name']

    if custom_fabric_variables['bgpPeerGroupSettings']['evpnOverlayPeerGroup']['password']:
        fabric_variables['bgp_peer_groups']['EVPN_OVERLAY_PEERS']['password'] \
            = custom_fabric_variables['bgpPeerGroupSettings']['evpnOverlayPeerGroup']['password']

    if custom_fabric_variables['bgpPeerGroupSettings']['mlagIPv4PeerGroup']['name']:
        fabric_variables['bgp_peer_groups']['MLAG_IPv4_UNDERLAY_PEER']['name'] \
            = custom_fabric_variables['bgpPeerGroupSettings']['mlagIPv4PeerGroup']['name']

    if custom_fabric_variables['bgpPeerGroupSettings']['mlagIPv4PeerGroup']['password']:
        fabric_variables['bgp_peer_groups']['MLAG_IPv4_UNDERLAY_PEER']['password'] \
            = custom_fabric_variables['bgpPeerGroupSettings']['mlagIPv4PeerGroup']['password']

    # Update interface_descriptions
    # Router ID Interface
    if custom_fabric_variables['interfaceDescriptions']['routerIdInterface']:
        fabric_variables['interface_descriptions']['router_id_interface'] = \
            custom_fabric_variables['interfaceDescriptions']['routerIdInterface']
    # VTEP Source Interface
    if custom_fabric_variables['interfaceDescriptions']['vtepSourceInterface']:
        fabric_variables['interface_descriptions']['vtep_source_interface'] = \
            custom_fabric_variables['interfaceDescriptions']['vtepSourceInterface']
    # Transit L3 Ethernet Interfaces
    if custom_fabric_variables['interfaceDescriptions']['transitL3EthernetInterfaces']:
        fabric_variables['interface_descriptions']['underlay_l3_ethernet_interfaces'] = \
            custom_fabric_variables['interfaceDescriptions']['transitL3EthernetInterfaces']
    # Transit L2 Ethernet Interfaces
    if custom_fabric_variables['interfaceDescriptions']['transitL2EthernetInterfaces']:
        fabric_variables['interface_descriptions']['underlay_l2_ethernet_interfaces'] = \
            custom_fabric_variables['interfaceDescriptions']['transitL2EthernetInterfaces']
    # Transit Port-Channels
    if custom_fabric_variables['interfaceDescriptions']['transitPortChannelInterfaces']:
        fabric_variables['interface_descriptions']['underlay_port_channel_interfaces'] = \
            custom_fabric_variables['interfaceDescriptions']['transitPortChannelInterfaces']
    # MLAG Peer Link
    if custom_fabric_variables['interfaceDescriptions']['mlagPeerLink']:
        fabric_variables['interface_descriptions']['mlag_port_channel_interface'] = \
            custom_fabric_variables['interfaceDescriptions']['mlagPeerLink']
    # MLAG Peer Link Member Interfaces
    if custom_fabric_variables['interfaceDescriptions']['mlagPeerLinkMemberInterfaces']:
        fabric_variables['interface_descriptions']['mlag_ethernet_interfaces'] = \
            custom_fabric_variables['interfaceDescriptions']['mlagPeerLinkMemberInterfaces']

    # Update point to point settings
    if custom_fabric_variables['p2pInterfaceSettings'] and custom_fabric_variables['p2pInterfaceSettings']['interfaceDefaults']:
        fabric_variables['p2p_interface_settings'] = custom_fabric_variables['p2pInterfaceSettings']['interfaceDefaults']

    return


def get_switch_basics(device_id, data_center_resolver, assert_tag_error=False):
    # Initialize switch_facts values
    switch_facts = {}
    switch_facts['serial_number'] = device_id

    # Get facts from tags
    switch_facts['hostname'] = [dev.hostName for dev in ctx.topology.getDevices(deviceIds=[device_id])][0]
    switch_facts['platform'] = [dev.modelName for dev in ctx.topology.getDevices(deviceIds=[device_id])][0]
    switch_facts['data_center'] = dc_dict.get(device_id)

    # Process Studio inputs
    try:
        data_center = data_center_resolver.resolve(device=device_id)['dataCenter']
        pod = data_center['pods'].resolve(device=device_id)['pod']
        super_spine_plane = data_center['superSpinePlanes'].resolve(device=device_id)['superSpinePlane']
    except Exception:
        return

    # First attempt to set switch role
    switch_role = None
    potential_roles = {"Leaf": "l3leaf", "Spine": "spine", "L2-Leaf": "l2leaf", "Super-Spine": "super_spine"}
    roles_applied_to_switch = role_dict.get(device_id)
    # If no role is applied to switch, switch should not be in fabric build
    if roles_applied_to_switch is None:
        return
    # Check to see that a device isn't assigned multiple roles within the L3 Leaf-Spine fabric
    roles_intersect = [role for role in roles_applied_to_switch if role in list(potential_roles.keys())]
    assert len(roles_intersect) <= 1, f"Only 1 data center role should be applied to the switch. " \
                                    f"Detected the following roles applied to {switch_facts['hostname']}: {roles_intersect}"
    # Set role
    if roles_applied_to_switch is not None:
        for role in potential_roles.keys():
            if role in roles_applied_to_switch:
                switch_role = role
                break

    switch_facts['type'] = potential_roles.get(switch_role)

    # Set switch id based on mini-tagger values
    node_id = node_id_dict.get(device_id)
    # If node_id tag isn't set, check for old leaf-number or spine-number tags
    if node_id is None and switch_facts.get('type') in ["l3leaf", "spine"]:
        id_tag_type = {"l3leaf": "Leaf-Number", "spine": "Spine-Number"}
        node_id = get_tag_value(device_id=device_id, label=id_tag_type[switch_facts['type']], workspace_id=workspace_id)

    if node_id is not None:
        switch_facts['id'] = int(node_id)

    # Check to see if switch is a member of a pod
    if pod is not None:
        if assert_tag_error:
            assert switch_role is not None, f"Make sure {switch_facts['hostname']} is tagged with a " \
                f"valid switch role. Valid switch roles are 'Leaf', 'Spine', 'Super-Spine', and 'L2-Leaf'."
        # Set DC-Pod tag value
        switch_facts['pod'] = pod_dict.get(device_id)
        # Get l3leaf and l2leaf resolvers
        l3_leaf_domain = pod['LeafDomains'].resolve(device=device_id)['l3LeafDomain']
        l2_leaf_domain = pod['l2LeafDomains'].resolve(device=device_id)['l2LeafDomain']
        # Check to see if leaf domain resolves
        if switch_facts['type'] == "l3leaf":
            # Set leaf domain
            l3_leaf_domain_id = l3_leaf_domain_dict.get(device_id)
            assert l3_leaf_domain is not None, f"There is no input resolver present for Leaf-Domain {l3_leaf_domain_id} in PoD {switch_facts['pod']}. " \
                                                f"Either enter an input for Leaf-Domain {l3_leaf_domain_id} in PoD {switch_facts['pod']} or " \
                                                f"remove the 'Leaf-Domain:{l3_leaf_domain_id}' tag from all devices in this pod."
            if l3_leaf_domain_id is None:
                return
            switch_facts['l3_leaf_domain'] = l3_leaf_domain_id
            switch_facts['group_index'] = int(switch_facts['l3_leaf_domain']) - 1
            # Set evpn role to client
            switch_facts['evpn_role'] = "client"

        # Check to see if l2 leaf domain resolves
        elif switch_facts['type'] == "l2leaf":
            # Set leaf domain
            l2_leaf_domain_id = l2_leaf_domain_dict.get(device_id)
            assert l2_leaf_domain is not None, f"There is no input resolver present for L2-Leaf-Domain {l2_leaf_domain_id} in PoD {switch_facts['pod']}. " \
                                                f"Either enter an input for L2-Leaf-Domain {l2_leaf_domain_id} in PoD {switch_facts['pod']} or " \
                                                f"remove the 'L2-Leaf-Domain:{l2_leaf_domain_id}' tag from all devices in this pod."
            if l2_leaf_domain_id is None:
                return
            switch_facts['l2_leaf_domain'] = l2_leaf_domain_id
            switch_facts['group_index'] = int(switch_facts['l2_leaf_domain']) - 1
            # Set evpn role to None
            switch_facts['evpn_role'] = None

        # If neither resolves, assume the switch is a spine
        elif switch_facts['type'] == "spine":
            # Set evpn role to server
            switch_facts['evpn_role'] = "server"

    # Check to see if switch is a member of a super-spine plane
    elif super_spine_plane is not None:
        if assert_tag_error:
            assert switch_role is not None, f"Make sure {switch_facts['hostname']} is tagged with a " \
                f"valid switch role. Valid switch roles are 'Leaf', 'Spine', 'Super-Spine', and 'L2-Leaf'."

        switch_facts['type'] = "super_spine"
        # Set super-spine plane id tag value
        switch_facts['super_spine_plane'] = super_spine_plane_dict.get(device_id)
        # Set evpn role to server
        switch_facts['evpn_role'] = "server"
    else:
        return

    # If node id not found
    if not switch_facts.get('id'):
        return
    # if switch is tagged with dc and dc-pod tag, but there is no input for the dc-pod resolver
    elif pod is None and super_spine_plane is None:
        return

    # Set properties of switch
    if switch_facts['type'] == "spine":
        switch_facts['uplink_type'] = "p2p"
        switch_facts['underlay_router'] = True
        switch_facts['vtep'] = False
        switch_facts['network_services'] = {
            "l2": False,
            "l3": True
        }
        switch_facts['mlag_support'] = False
    elif switch_facts['type'] == "l3leaf":
        switch_facts['uplink_type'] = "p2p"
        switch_facts['underlay_router'] = True
        switch_facts['vtep'] = True
        switch_facts['network_services'] = {
            "l2": True,
            "l3": True
        }
        switch_facts['mlag_support'] = True
    elif switch_facts['type'] == "l2leaf":
        switch_facts['uplink_type'] = "port-channel"
        switch_facts['underlay_router'] = False
        switch_facts['vtep'] = False
        switch_facts['network_services'] = {
            "l2": True,
            "l3": False
        }
        switch_facts['mlag_support'] = True
    elif switch_facts['type'] == "super_spine":
        switch_facts['uplink_type'] = None
        switch_facts['underlay_router'] = True
        switch_facts['vtep'] = False
        switch_facts['network_services'] = {
            "l2": False,
            "l3": True
        }
        switch_facts['mlag_support'] = False

    # Set bgp as if it is relevant
    switch_facts = set_bgp_as_from_studio_input(switch_facts, data_center_resolver)

    # Return switch_facts
    return switch_facts


def get_switches_in_my_data_center_basics(switch_facts, pod_only=False):
    # Dictionary of switches that will be returned
    switches_in_data_center = {}

    # Create tagstub
    tsclient = ctx.getApiClient(tsgr.TagSearchStub)

    # Get switches in same data center
    query = f"DC:\"{switch_facts['data_center']}\""
    tagmr = tspb.TagMatchRequestV2(query=query, workspace_id=workspace_id, topology_studio_request=True)
    tagmresp = tsclient.GetTagMatchesV2(tagmr)
    for match in tagmresp.matches:
        switch_in_data_center_facts = get_switch_basics(match.device.device_id, dataCenters)
        if switch_in_data_center_facts is None:
            continue
        if match.device.device_id not in switches_in_data_center:
            switches_in_data_center[match.device.device_id] = switch_in_data_center_facts

    # if pod_only is true, other leaf and spine switches outside of my_switch's pod will be removed
    if pod_only is True:
        leafs_and_spines_outside_of_my_pod = []
        for switch_facts in switches_in_data_center.values():
            if switch_facts['type'] != "super_spine" and switch_facts['pod'] != my_switch_facts['pod']:
                leafs_and_spines_outside_of_my_pod.append(switch_facts['serial_number'])
        for switch_id in leafs_and_spines_outside_of_my_pod:
            del switches_in_data_center[switch_id]
    return switches_in_data_center


def overlapping_networks_check(networks):
    for i in range(len(networks)):
        if networks[i].strip() == "":
            continue
        network1 = ipaddress.ip_network(networks[i])
        j = i+1
        while j < len(networks):
            network2 = ipaddress.ip_network(networks[j])
            assert not network1.overlaps(network2), f"Invalid underlay fabric subnets: " \
                f"subnets {network1.exploded} {network2.exploded} overlap"
            j += 1


def duplicate_node_id_check(switch_in_data_center_facts):
    """
    Checks to see if there is a duplicate node id value for node type in a Pod or DC
    """
    data_center = {
        "pods": {},
        "super_spine_planes": {}
    }
    # Sort node ids
    for switch_facts in switch_in_data_center_facts.values():
        if switch_facts.get('pod'):
            pod_name = switch_facts['pod']
            if not data_center['pods'].get(pod_name):
                # Create pod Dictionary
                data_center['pods'][pod_name] = {
                    "l3leaf": [],
                    "spine": [],
                    "l2leaf": [],
                }
            # Update dictionary
            data_center['pods'][pod_name][switch_facts['type']].append(switch_facts['id'])
        if switch_facts.get('super_spine_plane'):
            super_spine_plane_name = switch_facts['super_spine_plane']
            if not data_center['super_spine_planes'].get(super_spine_plane_name):
                # Create ss_plane Dictionary
                data_center['super_spine_planes'][super_spine_plane_name] = []
            # Update dictionary
            data_center['super_spine_planes'][super_spine_plane_name].append(switch_facts['id'])
    # Check for no duplicate ids by comparing the length of all node_ids per node type
    # per pod found with the number of unique node_ids per node type per pod found
    # (these numbers should be equal if all node_ids are unique)
    for pod, node_dict in data_center['pods'].items():
        for node_type, node_ids in node_dict.items():
            assert len(node_ids) == len(set(node_ids)), f"Duplicate {node_type} node IDs detected in pod {pod}"
    # Check for no duplicate ids by comparing the length of all node_ids per super-spine type
    # per dc found with the number of unique node_ids per super-spine type per dc found
    # (these numbers should be equal if all node_ids are unique)
    for super_spine_plane, node_ids in data_center['super_spine_planes'].items():
        assert len(node_ids) == len(set(node_ids)), \
            f"Duplicate super-spine node IDs detected in super-spine plane {super_spine_plane}"


def clean_config(config):
    if config.get('router_bgp'):
        if len(config["router_bgp"]["address_family_evpn"]["peer_groups"]) == 0:
            config["router_bgp"]["address_family_evpn"] = None
    return config

# returns dictionary of device to label value based on workspace tag assignments
def populate_single_value_dict(workspace_id, label):
    sv_dict = {}
    tsclient = ctx.getApiClient(tsgr.TagSearchStub)
    tvsr = tspb.TagValueSearchRequest(
        label=label,
        workspace_id=workspace_id,
        topology_studio_request=True
    )
    for tag in tsclient.GetTagValueSuggestions(tvsr).tags:
        query = f"{tag.label}:\"{tag.value}\""
        tagmr = tspb.TagMatchRequestV2(
            query=query,
            workspace_id=workspace_id,
            topology_studio_request=True
        )
        tagmresp = tsclient.GetTagMatchesV2(tagmr)
        for match in tagmresp.matches:
            assert tag.label not in ["NodeId", "Leaf-Domain"] or \
                tag.value.isdigit(), \
                f"The value of tag '{tag.label}' assigned to device " \
                f"{match.device.device_id} must be an integer. " \
                f"The current value is '{tag.value}'"
            assert match.device.device_id not in sv_dict or \
                sv_dict[match.device.device_id] == tag.value, \
                f"The device {match.device.device_id} must be applied to only " \
                f"one value of tag '{tag.label}'.  Currently applied to values of " \
                f"'{sv_dict[match.device.device_id]}' and '{tag.value}'"
            sv_dict[match.device.device_id] = tag.value
    return sv_dict

# returns dictionary of device to label values based on workspace tag assignments
def populate_multi_value_dict(workspace_id, label):
    mv_dict = {}
    tsclient = ctx.getApiClient(tsgr.TagSearchStub)
    tvsr = tspb.TagValueSearchRequest(
        label=label,
        workspace_id=workspace_id,
        topology_studio_request=True
    )
    for tag in tsclient.GetTagValueSuggestions(tvsr).tags:
        query = f"{tag.label}:\"{tag.value}\""
        tagmr = tspb.TagMatchRequestV2(
            query=query,
            workspace_id=workspace_id,
            topology_studio_request=True
        )
        tagmresp = tsclient.GetTagMatchesV2(tagmr)
        for match in tagmresp.matches:
            mv_dict.setdefault(match.device.device_id, []).append(tag.value)
    return mv_dict


# return asn lists used in set_switch_facts
def prepare_leaf_asn_lists(device_id, data_center_resolver):
    data_center = data_center_resolver.resolve(device=device_id)['dataCenter']
    pod = data_center['pods'].resolve(device=device_id)['pod']
    range_asns = string_to_list(str(pod['commonBGPConfig']['leafAsnRange']))
    # remove any bgp_as numbers set in leaf_domain input of studio
    manual_asns = {}
    if len(range_asns) > 1:
        for switch_in_my_dc_facts in switches_in_my_data_center.values():
            if switch_in_my_dc_facts.get('bgp_as') is not None and \
                    switch_in_my_dc_facts['bgp_as'] in range_asns:
                if int(switch_in_my_dc_facts['group_index']) not in manual_asns:
                    manual_asns[int(switch_in_my_dc_facts['group_index'])] = \
                        switch_in_my_dc_facts['bgp_as']
                    range_asns.remove(switch_in_my_dc_facts['bgp_as'])
    return range_asns, manual_asns

start_time = time.time()

# Get studio info from ctx
my_device = ctx.getDevice()
workspace_id = ctx.studio.workspaceId

# Pre-populate dictionaries used for getting switch facts
dc_dict = populate_single_value_dict(workspace_id, 'DC')
role_dict = populate_multi_value_dict(workspace_id, 'Role')
node_id_dict = populate_single_value_dict(workspace_id, 'NodeId')
pod_dict = populate_single_value_dict(workspace_id, 'DC-Pod')
l3_leaf_domain_dict = populate_single_value_dict(workspace_id, 'Leaf-Domain')
l2_leaf_domain_dict = populate_single_value_dict(workspace_id, 'L2-Leaf-Domain')
super_spine_plane_dict = populate_single_value_dict(workspace_id, 'Super-Spine-Plane')

# Initialize variables
my_switch_facts = {}
my_config = {}

# Set basic switch facts for my_switch
my_switch_facts = get_switch_basics(my_device.id, dataCenters, assert_tag_error=True)

if my_switch_facts is None:
    return

# Set fabric_variables
set_fabric_variables(dataCenters)

# Set basic switch facts for all switches in the same data center as my_switch
if my_switch_facts['type'] != "super_spine":
    switches_in_my_data_center = get_switches_in_my_data_center_basics(my_switch_facts, pod_only=True)
else:
    switches_in_my_data_center = get_switches_in_my_data_center_basics(my_switch_facts)

# Check for duplicate node_id tags
duplicate_node_id_check(switches_in_my_data_center)

# Get manually set asns for all leafs by getting leaf_asns and manually_set_leaf_domains for just one leaf switch
leaf_asns = {}
manually_set_leaf_domains = {}
for switch_facts in switches_in_my_data_center.values():
    if switch_facts['type'] != "l3leaf":
        continue
    pod = switch_facts['pod']
    if pod in leaf_asns:
        continue
    leaf_asns[pod], manually_set_leaf_domains[pod] = prepare_leaf_asn_lists(switch_facts['serial_number'], dataCenters)

# get my switch's neighbors
uplink_interfaces, downlink_interfaces, mlag_peer_link_interfaces = get_interfaces_info(my_switch_facts)
my_switch_facts['uplink_interfaces'] = uplink_interfaces
my_switch_facts['downlink_interfaces'] = downlink_interfaces
my_switch_facts['mlag_peer_link_interfaces'] = mlag_peer_link_interfaces
# Merge multilane interfaces
my_switch_facts = merge_multilane_interfaces(my_switch_facts)
# Put all switch neighbors in one list to easily check if a switch in switch_in_my_data_center is a neighbor
my_switch_facts['neighbors'] = [iface['neighborId'] for iface in my_switch_facts['uplink_interfaces'].values()] \
    + [iface['neighborId'] for iface in my_switch_facts['downlink_interfaces'].values()] \
    + [iface['neighborId'] for iface in my_switch_facts['mlag_peer_link_interfaces'].values()]

# Update my_switch_facts key for switches_in_my_data_center
switches_in_my_data_center[my_switch_facts['serial_number']] = my_switch_facts

# Set interface info for spines and neighbors from studio topology tags
for switch_facts in switches_in_my_data_center.values():
    # If this switch is a neighbor of my_switch or (my_switch is not a super-spine and
    # this switch is in the same pod as my_switch and it's a spine) get interface info
    if (switch_facts['serial_number'] in my_switch_facts['neighbors']) \
            or (my_switch_facts.get('pod') and switch_facts.get('pod')
                and my_switch_facts['pod'] == switch_facts['pod']
                and switch_facts['type'] == "spine"):
        uplink_interfaces, downlink_interfaces, mlag_peer_link_interfaces = get_interfaces_info(switch_facts)
        switch_facts['uplink_interfaces'] = uplink_interfaces
        switch_facts['downlink_interfaces'] = downlink_interfaces
        switch_facts['mlag_peer_link_interfaces'] = mlag_peer_link_interfaces
        # Merge multilane interfaces
        switch_facts = merge_multilane_interfaces(switch_facts)

# Need to get and set maximums for all switches in the event that the topology has a super-spine
# and bgp evpn peering (router_ids need max super_spine and max_spine values when same subnet is used)
# When getting max uplink switches and max parallel uplinks, we'll only be able to get maximums
# on a spine switch and only need to do so on one spine switch.
# This needs to come after set_switch_facts() is done because we need to access properties which
# that function sets.
# Set maximums on all switches in pod
for switch_facts in switches_in_my_data_center.values():
    switch_facts = set_maximums(switch_facts)

# Clear evpn roles on switches if necessary
for switch_facts in switches_in_my_data_center.values():
    switch_facts = clear_evpn_role(switch_facts, dataCenters)

# Set switch facts for super-spines and all switches in my pod (besides lists of evpn route servers/clients)
for switch_facts in switches_in_my_data_center.values():
    if my_switch_facts['type'] == "super_spine" \
            or switch_facts['type'] == "super_spine" \
            or switch_facts.get('pod') == my_switch_facts.get('pod'):
        switch_facts = set_switch_facts(switch_facts, dataCenters)

# Set topology facts ( in order to set transit p2p and port-channel links )
for switch_facts in switches_in_my_data_center.values():
    if switch_facts['serial_number'] == my_switch_facts['serial_number'] \
            or switch_facts['serial_number'] in my_switch_facts['neighbors']:
        switch_facts = set_topology_facts(switch_facts)

# Set evpn servers (clients will be set within set_overlay_config() function)
evpn_route_server_serial_numbers = [switch_facts['serial_number'] for switch_facts in switches_in_my_data_center.values()
                                    if switch_facts['evpn_role'] is not None and switch_facts['evpn_role'] == "server"]
for switch_facts in switches_in_my_data_center.values():
    if switch_facts['evpn_role'] is not None and switch_facts['evpn_role'] == "client":
        switch_facts['evpn_route_server_ids'] = evpn_route_server_serial_numbers

# Update my_switch_facts
my_switch_facts = switches_in_my_data_center[my_device.id]

# Set underlay/overlay routing protocol for super_spine switches if necessary
if my_switch_facts['type'] == "super_spine" and len(my_switch_facts['downlink_switches_ids']) > 0:
    downlink_switch = switches_in_my_data_center[my_switch_facts['downlink_switches_ids'][0]]
    my_switch_facts['underlay_routing_protocol'] = switches_in_my_data_center[my_switch_facts['downlink_switches_ids']
                                                                            [0]]['underlay_routing_protocol']
    my_switch_facts['overlay_routing_protocol'] = switches_in_my_data_center[my_switch_facts['downlink_switches_ids']
                                                                            [0]].get('overlay_routing_protocol')
    # Set ospf relevant config if necessary
    if my_switch_facts['underlay_routing_protocol'] == "ospf":
        my_switch_facts['underlay_ospf_process_id'] = downlink_switch['underlay_ospf_process_id']
        my_switch_facts['underlay_ospf_area'] = downlink_switch['underlay_ospf_area']
        my_switch_facts['underlay_ospf_max_lsa'] = downlink_switch['underlay_ospf_max_lsa']
        my_switch_facts['underlay_ospf_bfd_enable'] = downlink_switch['underlay_ospf_bfd_enable']

# Set structured config
my_config = {
    "spanning_tree": {},
    "vlans": {},
    "vlan_interfaces": {},
    "port_channel_interfaces": {},
    "ethernet_interfaces": {},
    "loopback_interfaces": {},
    "prefix_lists": {},
    "route_maps": {},
    # Will look into setting bfd config in later version of studio; for now users should set with static configlet
    # "router_bfd": {},
    #Ahmad added this
    "router_multicast": {},
    "peer_filters": {},
    "router_bgp": {
        "peer_groups": {},
        "address_family_ipv4": {
            "peer_groups": {}
        },
        "address_family_evpn": {
            "peer_groups": {}
        },
        #Ahmad added this
        "address_family_rt": {
            "peer_groups": {}
        },
        "neighbor_interfaces": {},
        "neighbors": {},
        "redistribute_routes": {
            "connected": {}
        }
    },
    "router_ospf": {
        "process_ids": {}
    }
}
# Update tags from my_switch_facts values
update_tags(my_switch_facts)
# Set config from my_switch_facts values
my_config = set_base_config(my_config, my_switch_facts)
my_config = set_mlag_config(my_config, my_switch_facts)
my_config = set_underlay_config(my_config, my_switch_facts)
my_config = set_overlay_config(my_config, my_switch_facts)
my_config = set_vxlan_config(my_config, my_switch_facts)
my_config = clean_config(my_config)

config = my_config

preconfig_time = time.time() - start_time
ctx.info(f"time taken to prepare config: {preconfig_time} seconds")

%>
% if config.get("service_routing_protocols_model") is not None and config.get("service_routing_protocols_model") == "multi-agent":
service routing protocols model multi-agent
!
% endif
## eos - spanning-tree
% if config.get("spanning_tree") is not None:
%     if config["spanning_tree"].get("mode") is not None:
spanning-tree mode ${ config["spanning_tree"].get("mode") }
%     endif
%     if config["spanning_tree"].get("no_spanning_tree_vlan") is not None:
no spanning-tree vlan-id ${ config["spanning_tree"].get("no_spanning_tree_vlan") }
%     endif
!
% endif
## eos - VLANs
%if config.get("vlans") is not None:
%     for vlan in natural_sort(config.get("vlans")):
vlan ${ vlan }
%          if config.get("vlans")[vlan].get("name") is not None:
name ${ config.get("vlans")[vlan].get("name") }
%          endif
%          if config.get("vlans")[vlan].get("state") is not None:
state ${ config.get("vlans")[vlan].get("state") }
%          endif
%          if config.get("vlans")[vlan].get("trunk_groups") is not None:
%               for trunk_group in config.get("vlans")[vlan].get("trunk_groups"):
trunk group ${ trunk_group }
%               endfor
%          endif
!
%    endfor %}
%endif
## eos- Port-Channel Interfaces
% if config.get("port_channel_interfaces") is not None:
%   for port_channel_interface in natural_sort(config["port_channel_interfaces"].keys()):
interface ${ port_channel_interface }
%     if config["port_channel_interfaces"][port_channel_interface].get("description") is not None:
description ${ config["port_channel_interfaces"][port_channel_interface]["description"] }
%     endif
%     if config["port_channel_interfaces"][port_channel_interface].get("shutdown") == True:
shutdown
%     elif config["port_channel_interfaces"][port_channel_interface].get("shutdown") == False:
no shutdown
%     endif
%     if config["port_channel_interfaces"][port_channel_interface].get("mtu") is not None:
mtu ${ config["port_channel_interfaces"][port_channel_interface]["mtu"] }
%     endif
%     if config["port_channel_interfaces"][port_channel_interface].get("type") is not None and config["port_channel_interfaces"][port_channel_interface].get("type") == "routed":
no switchport
%     else:
switchport
%     endif
%     if config["port_channel_interfaces"][port_channel_interface].get("mode") is not None and config["port_channel_interfaces"][port_channel_interface].get("mode") == "access":
switchport access vlan ${ config["port_channel_interfaces"][port_channel_interface]["vlans"] }
%     endif %}
%     if config["port_channel_interfaces"][port_channel_interface].get("mode") is not None and config["port_channel_interfaces"][port_channel_interface].get("mode") == "trunk":
switchport mode ${ config["port_channel_interfaces"][port_channel_interface]["mode"] }
%     endif
%     if config["port_channel_interfaces"][port_channel_interface].get("trunk_groups") is not None:
%       for trunk_group in config["port_channel_interfaces"][port_channel_interface]["trunk_groups"]:
switchport trunk group ${ trunk_group }
%       endfor
%     endif
!
%   endfor
% endif
## eos - Ethernet Interfaces
%if config.get("ethernet_interfaces") is not None:
%for ethernet_interface in natural_sort(config["ethernet_interfaces"].keys()):
interface ${ethernet_interface }
%     if config["ethernet_interfaces"][ethernet_interface]["description"] is not None:
description ${config["ethernet_interfaces"][ethernet_interface]["description"]}
%     endif
%     if config["ethernet_interfaces"][ethernet_interface].get("channel_group") is not None:
channel-group ${ config["ethernet_interfaces"][ethernet_interface]["channel_group"]["id"] } mode ${ config["ethernet_interfaces"][ethernet_interface]["channel_group"]["mode"] }
%     else:
%         if config["ethernet_interfaces"][ethernet_interface].get("mtu") is not None:
mtu ${ config["ethernet_interfaces"][ethernet_interface]["mtu"] }
%         endif
%         if config["ethernet_interfaces"][ethernet_interface].get("type") is not None and config["ethernet_interfaces"][ethernet_interface].get("type") == "routed":
no switchport
%         else:
switchport
%         endif
%         if config["ethernet_interfaces"][ethernet_interface].get("mode") is not None and config["ethernet_interfaces"][ethernet_interface].get("mode") == "access":
%             if config["ethernet_interfaces"][ethernet_interface].get("vlans") is not None:
switchport access vlan ${ config["ethernet_interfaces"][ethernet_interface].get("vlans") }
%             endif
%         endif
%         if config["ethernet_interfaces"][ethernet_interface].get("mode") is not None and config["ethernet_interfaces"][ethernet_interface].get("mode") == "trunk":
%             if config["ethernet_interfaces"][ethernet_interface].get("vlans") is not None:
switchport trunk allowed vlan ${ config["ethernet_interfaces"][ethernet_interface].get("vlans") }
%             endif
%             if config["ethernet_interfaces"][ethernet_interface].get("native_vlan") is not None:
switchport trunk native vlan ${ config["ethernet_interfaces"][ethernet_interface].get("native_vlan") }
%             endif
%         endif
%         if config["ethernet_interfaces"][ethernet_interface].get("mode") is not None:
switchport mode ${ config["ethernet_interfaces"][ethernet_interface].get("mode") }
%         endif
%         if config["ethernet_interfaces"][ethernet_interface].get("trunk_groups") is not None:
%             for trunk_group in config["ethernet_interfaces"][ethernet_interface].get("trunk_groups"):
switchport trunk group ${ trunk_group }
%             endfor
%         endif
%         if config["ethernet_interfaces"][ethernet_interface].get("vrf") is not None:
vrf ${ config["ethernet_interfaces"][ethernet_interface].get("vrf") }
%         endif
%         if config["ethernet_interfaces"][ethernet_interface].get("ip_address") is not None:
ip address ${ config["ethernet_interfaces"][ethernet_interface].get("ip_address") }
%             if config["ethernet_interfaces"][ethernet_interface].get("ip_address_secondaries") is not None:
%                 for ip_address_secondary in config["ethernet_interfaces"][ethernet_interface].get("ip_address_secondaries"):
ip address ${ ip_address_secondary } secondary
%                 endfor
%             endif
%         endif
## Ahmad Adding PIM Config to Ethernet Interfaces
%         if config["ethernet_interfaces"][ethernet_interface].get("pim"):
%           if config["ethernet_interfaces"][ethernet_interface]["pim"]["ipv4"].get("sparse_mode"):
pim ipv4 sparse-mode
%           endif
%         endif
%         if config["ethernet_interfaces"][ethernet_interface].get("ospf_network_point_to_point"):
ip ospf network point-to-point
%         endif
%         if config["ethernet_interfaces"][ethernet_interface].get("ospf_area"):
ip ospf area ${ config["ethernet_interfaces"][ethernet_interface]["ospf_area"] }
%         endif
%     endif
%     for iface_default in config["ethernet_interfaces"][ethernet_interface].get('defaults', []):
${iface_default}
%     endfor
!
%endfor
%endif
## eos - Loopback Interfaces
%if config.get("loopback_interfaces") is not None:
%   for loopback_interface in natural_sort(config.get("loopback_interfaces").keys()):
interface ${ loopback_interface }
%       if config["loopback_interfaces"][loopback_interface].get("description") is not None:
description ${ config["loopback_interfaces"][loopback_interface].get("description") }
%       endif
%       if config["loopback_interfaces"][loopback_interface].get("shutdown") is not None and config["loopback_interfaces"][loopback_interface].get("shutdown") == True:
shutdown
%       elif config["loopback_interfaces"][loopback_interface].get("shutdown") is not None and config["loopback_interfaces"][loopback_interface].get("shutdown") == False:
no shutdown
%       endif
%       if config["loopback_interfaces"][loopback_interface].get("vrf") is not None:
vrf ${ config["loopback_interfaces"][loopback_interface].get("vrf") }
%       endif
%       if config["loopback_interfaces"][loopback_interface].get("ip_address") is not None:
ip address ${ config["loopback_interfaces"][loopback_interface].get("ip_address") }
%           if config["loopback_interfaces"][loopback_interface].get("ip_address_secondaries") is not None:
%               for ip_address_secondary in config["loopback_interfaces"][loopback_interface].get("ip_address_secondaries"):
ip address ${ ip_address_secondary } secondary
%               endfor
%           endif
%       endif
%       if config["loopback_interfaces"][loopback_interface].get("ospf_area"):
ip ospf area ${ config["loopback_interfaces"][loopback_interface]["ospf_area"] }
%       endif
!
%   endfor
%endif
## eos - VLAN Interfaces
% if config.get("vlan_interfaces") is not None:
%   for vlan_interface in natural_sort(config.get("vlan_interfaces").keys()):
interface ${ vlan_interface }
%     if config.get("vlan_interfaces")[vlan_interface].get("description") is not None:
description ${ config.get("vlan_interfaces")[vlan_interface].get("description") }
%     endif
%     if config.get("vlan_interfaces")[vlan_interface].get("shutdown") == True:
shutdown
%     elif config.get("vlan_interfaces")[vlan_interface].get("shutdown") == False:
no shutdown
%     endif
%     if config.get("vlan_interfaces")[vlan_interface].get("mtu") is not None:
mtu ${ config.get("vlan_interfaces")[vlan_interface].get("mtu") }
%     endif
%     if config.get("vlan_interfaces")[vlan_interface].get("no_autostate") == True:
no autostate
%     endif
%     if config.get("vlan_interfaces")[vlan_interface].get("vrf") is not None:
vrf ${ config.get("vlan_interfaces")[vlan_interface].get("vrf") }
%     endif
%     if config.get("vlan_interfaces")[vlan_interface].get("ip_address") is not None:
ip address ${ config.get("vlan_interfaces")[vlan_interface].get("ip_address") }
%         if config.get("vlan_interfaces")[vlan_interface].get("ip_address_secondaries") is not None:
%             for ip_address_secondary in config.get("vlan_interfaces")[vlan_interface].get("ip_address_secondaries"):
ip address ${ ip_address_secondary } secondary
%             endfor
%         endif
%     endif
%     if config.get("vlan_interfaces")[vlan_interface].get("ip_virtual_router_address") is not None:
ip virtual-router address ${ config.get("vlan_interfaces")[vlan_interface].get("ip_virtual_router_address") }
%     endif
%     if config.get("vlan_interfaces")[vlan_interface].get("ip_address_virtual") is not None:
ip address virtual ${ config.get("vlan_interfaces")[vlan_interface].get("ip_address_virtual") }
%     endif
%     if config.get("vlan_interfaces")[vlan_interface].get("ip_helpers") is not None:
%       for ip_helper in config.get("vlan_interfaces")[vlan_interface].get("ip_helpers").keys():
<%        ip_helper_cli = "ip helper-address " + ip_helper %>
%         if config.get("vlan_interfaces")[vlan_interface]["ip_helpers"][ip_helper].get("vrf") is not None:
<%            ip_helper_cli = ip_helper_cli + " vrf " + config.get("vlan_interfaces")[vlan_interface]["ip_helpers"][ip_helper].get("vrf") %>
%         endif
%         if config.get("vlan_interfaces")[vlan_interface]["ip_helpers"][ip_helper].get("source_interface") is not None:
<%            ip_helper_cli = ip_helper_cli + " source-interface " + config.get("vlan_interfaces")[vlan_interface]["ip_helpers"][ip_helper].get("source_interface") %>
%         endif %}
${ ip_helper_cli }
%       endfor
%      endif
%      if config.get("vlan_interfaces")[vlan_interface].get("ospf_network_point_to_point"):
ip ospf network point-to-point
%      endif
%      if config.get("vlan_interfaces")[vlan_interface].get("ospf_area"):
ip ospf area ${ config.get("vlan_interfaces")[vlan_interface]["ospf_area"] }
%      endif
## Ahmad adding PIM on SVIs
%      if config.get("vlan_interfaces")[vlan_interface].get("pim"):
%         if config.get("vlan_interfaces")[vlan_interface]["pim"]["ipv4"].get("sparse_mode"):
pim ipv4 sparse-mode
%         endif
%      endif
!
%   endfor
% endif
## vxlan-interfaces
% if config.get("vxlan_interface"):
interface Vxlan1
%     if config["vxlan_interface"]["Vxlan1"].get("vxlan"):
%         if config["vxlan_interface"]["Vxlan1"]["vxlan"].get("source_interface"):
vxlan source-interface ${ config["vxlan_interface"]["Vxlan1"]["vxlan"]["source_interface"] }
%         endif
## Ahmad Adding evpn_multicast config section to vxlan interface
%         if config["vxlan_interface"]["Vxlan1"]["vxlan"].get("mlag_source_interface"):
vxlan mlag source-interface ${ config["vxlan_interface"]["Vxlan1"]["vxlan"]["mlag_source_interface"] }
%         endif
%         if config["vxlan_interface"]["Vxlan1"]["vxlan"].get("virtual_router_encapsulation_mac_address"):
vxlan virtual-router encapsulation mac-address ${ config["vxlan_interface"]["Vxlan1"]["vxlan"]["virtual_router_encapsulation_mac_address"] }
%         endif
%         if config["vxlan_interface"]["Vxlan1"]["vxlan"].get("udp_port"):
vxlan udp-port ${ config["vxlan_interface"]["Vxlan1"]["vxlan"]["udp_port"] }
%         endif
%         if config["vxlan_interface"]["Vxlan1"]["vxlan"].get("vlans"):
%             for vlan in config["vxlan_interface"]["Vxlan1"]["vxlan"]["vlans"].keys():
vxlan vlan ${ vlan } vni ${ config["vxlan_interface"]["Vxlan1"]["vxlan"]["vlans"][vlan]["vni"] }
%             endfor
%         endif
%         if config["vxlan_interface"]["Vxlan1"]["vxlan"].get("vrfs"):
%             for vrf in config["vxlan_interface"]["Vxlan1"]["vxlan"]["vrfs"].keys():
vxlan vrf ${ vrf } vni ${ config["vxlan_interface"]["Vxlan1"]["vxlan"]["vrfs"][vrf]["vni"] }
%             endfor %}
%         endif
%     endif
!
% endif
## eos - tcam profile
% if config.get("tcam_profile") is not None:
hardware tcam
%     if config["tcam_profile"].get("profiles") is not None:
%         for profile in config["tcam_profile"]["profiles"].keys():
profile ${ profile }
${ config["tcam_profile"]["profiles"][profile] }
!
%         endfor
%     endif
%     if config["tcam_profile"].get("system") is not None:
system profile ${ config["tcam_profile"]["system"] }
%     endif
!
% endif
## eos - ip virtual router mac
% if config.get("ip_virtual_router_mac_address") is not None:
ip virtual-router mac-address ${ config["ip_virtual_router_mac_address"] }
!
% endif
## eos - IP Routing
% if config.get("ip_routing") == True:
ip routing
!
% elif config.get("ip_routing") == False:
no ip routing
!
% endif
## eos - VRFs
% if config.get("vrfs") is not None:
%   for vrf in config.get("vrfs"):
%       if config.get("vrfs")[vrf].get("ip_routing") is not None and config.get("vrfs")[vrf].get("ip_routing") == True  and vrf != 'default':
ip routing vrf ${ vrf }
%       elif config.get("vrfs")[vrf].get("ip_routing") is not None and config.get("vrfs")[vrf].get("ip_routing") == False  and vrf != 'default':
no ip routing vrf ${ vrf }
%       endif
%   endfor
!
% endif
## Ahmad eos - Router Multicast (From Campus Studio)
% if config.get("router_multicast"):
router multicast
%     if config["router_multicast"].get("ipv4"):
    ipv4
%         if config["router_multicast"]["ipv4"].get("routing"):
    routing
%         endif
%         if config["router_multicast"]["ipv4"].get("multipath"):
    multipath ${ config["router_multicast"]["ipv4"]["multipath"] }
%         endif
%         if config["router_multicast"]["ipv4"].get("software_forwarding"):
    software-forwarding ${ config["router_multicast"]["ipv4"]["software_forwarding"] }
%         endif
%     endif
##%     for vrf in natural_sort(config["router_multicast"].get("vrfs",[]), sort_key="name"):
##%         if vrf["name"] != "default":
##    vrf ${ vrf["name"] }
##%             if vrf.get("ipv4"):
##    ipv4
##%             endif
##%             if vrf["ipv4"].get("routing", False) is True:
##        routing
##%             endif
##    !
##%         endif
##%     endfor
!
% endif
## eos - prefix-lists
% if config.get("prefix_lists") is not None:
%    for prefix_list in config["prefix_lists"].keys():
ip prefix-list ${ prefix_list }
%       for sequence in config["prefix_lists"][prefix_list]["sequence_numbers"].keys():
%         if config["prefix_lists"][prefix_list]["sequence_numbers"][sequence].get("action") is not None:
seq ${ sequence } ${ config["prefix_lists"][prefix_list]["sequence_numbers"][sequence]["action"] }
%         endif
%       endfor
!
%    endfor
% endif
## eos - mlag configuration
% if config.get("mlag_configuration") is not None and config["mlag_configuration"].get("enabled") == True:
mlag configuration
%     if config["mlag_configuration"].get("domain_id") is not None:
domain-id ${ config["mlag_configuration"]["domain_id"] }
%     endif
%     if config["mlag_configuration"].get("local_interface") is not None:
local-interface ${ config["mlag_configuration"]["local_interface"] }
%     endif
%     if config["mlag_configuration"].get("peer_address") is not None:
peer-address ${ config["mlag_configuration"]["peer_address"] }
%     endif
%     if config["mlag_configuration"].get("peer_address_heartbeat") is not None:
%       if config["mlag_configuration"]["peer_address_heartbeat"].get("peer_ip") is not None:
%           if config["mlag_configuration"]["peer_address_heartbeat"].get("vrf") is not None and config["mlag_configuration"]["peer_address_heartbeat"].get("vrf") != 'default':
peer-address heartbeat ${ config["mlag_configuration"]["peer_address_heartbeat"]["peer_ip"] } vrf ${ config["mlag_configuration"]["peer_address_heartbeat"]["vrf"] }
## using the default VRF #}
%           else:
peer-address heartbeat ${ config["mlag_configuration"]["peer_address_heartbeat"]["peer_ip"] }
%           endif
%       endif
%     endif
%     if config["mlag_configuration"].get("peer_link") is not None:
peer-link ${ config["mlag_configuration"]["peer_link"] }
%     endif
%     if config["mlag_configuration"].get("dual_primary_detection_delay") is not None:
dual-primary detection delay ${ config["mlag_configuration"]["dual_primary_detection_delay"] } action errdisable all-interfaces
%     endif
%     if config["mlag_configuration"].get("reload_delay_mlag") is not None:
reload-delay mlag ${ config["mlag_configuration"]["reload_delay_mlag"] }
%     endif
%     if config["mlag_configuration"].get("reload_delay_non_mlag") is not None:
reload-delay non-mlag ${ config["mlag_configuration"]["reload_delay_non_mlag"] }
%     endif
!
% endif
## eos - Route Maps
% if config.get("route_maps") is not None:
%   for route_map in natural_sort(config["route_maps"].keys()):
%       for sequence in config["route_maps"][route_map]["sequence_numbers"].keys():
%           if config["route_maps"][route_map]["sequence_numbers"][sequence].get("type") is not None:
route-map ${ route_map } ${ config["route_maps"][route_map]["sequence_numbers"][sequence]["type"] } ${ sequence }
%               if config["route_maps"][route_map]["sequence_numbers"][sequence].get("description") is not None:
description ${ config["route_maps"][route_map]["sequence_numbers"][sequence]["description"] }
%               endif
%               if config["route_maps"][route_map]["sequence_numbers"][sequence].get("match") is not None:
%                   for match_rule in config["route_maps"][route_map]["sequence_numbers"][sequence]["match"]:
match ${ match_rule }
%                   endfor
%               endif
%               if config["route_maps"][route_map]["sequence_numbers"][sequence].get("set") is not None:
%                   for set_rule in config["route_maps"][route_map]["sequence_numbers"][sequence]["set"]:
set ${ set_rule }
%                   endfor
%               endif
!
%           endif
%       endfor
%   endfor
% endif
## eos - peer-filters
% if config.get("peer_filters") is not None:
%   for peer_filter in config["peer_filters"].keys():
peer-filter ${ peer_filter }
%     for sequence in config["peer_filters"][peer_filter]["sequence_numbers"].keys():
%         if config["peer_filters"][peer_filter]["sequence_numbers"][sequence].get("match") is not None:
${ sequence } match ${ config["peer_filters"][peer_filter]["sequence_numbers"][sequence]["match"] }
%         endif
%     endfor
!
%   endfor
% endif
## eos - Router bfd
% if config.get("router_bfd") is not None and config.get("router_bfd") != {}:
router bfd
%   if config["router_bfd"].get("multihop") is not None:
%     if config["router_bfd"]["multihop"].get("interval") is not None and config["router_bfd"]["multihop"].get("min_rx") is not None and config["router_bfd"]["multihop"].get("multiplier") is not None:
multihop interval ${ config["router_bfd"]["multihop"]["interval"] } min-rx ${ config["router_bfd"]["multihop"]["min_rx"] } multiplier ${ config["router_bfd"]["multihop"]["multiplier"] }
%     endif
%   endif
!
% endif
## eos - Router BGP
% if config.get("router_bgp") is not None:
% if config["router_bgp"].get("as") is not None:
router bgp ${ config["router_bgp"]["as"] }
%     if config["router_bgp"].get("router_id") is not None:
router-id ${ config["router_bgp"]["router_id"] }
%     endif
%     if config["router_bgp"].get("maximum_paths"):
<% max_paths_cli = "maximum-paths {} ".format(config["router_bgp"]["maximum_paths"]) %>
%        if config["router_bgp"].get("ecmp"):
<% max_paths_cli += "ecmp {}".format(config["router_bgp"]["ecmp"]) %>
        % endif
${max_paths_cli}
%     endif
%     if config["router_bgp"].get("peer_groups") is not None:
%       for peer_group in config["router_bgp"]["peer_groups"].keys():
%         if config["router_bgp"]["peer_groups"][peer_group].get("bgp_listen_range_prefixes") is not None:
%           for bgp_listen_range_prefix in config["router_bgp"]["peer_groups"][peer_group]["bgp_listen_range_prefixes"].keys():
bgp listen range ${ bgp_listen_range_prefix } peer-group ${ peer_group } peer-filter ${ config["router_bgp"]["peer_groups"][peer_group]["bgp_listen_range_prefixes"][bgp_listen_range_prefix]["peer_filter"] }
%           endfor
%         endif
%       endfor
%     for peer_group in natural_sort(config["router_bgp"]["peer_groups"].keys()):
%         if config["router_bgp"]["peer_groups"][peer_group].get("description") is not None:
neighbor ${ peer_group } description ${ config["router_bgp"]["peer_groups"][peer_group]["description"] }
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("shutdown") == True:
neighbor ${ peer_group } shutdown
%         endif
neighbor ${ peer_group } peer group
%         if config["router_bgp"]["peer_groups"][peer_group].get("remote_as") is not None:
neighbor ${ peer_group } remote-as ${ config["router_bgp"]["peer_groups"][peer_group]["remote_as"] }
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("local_as") is not None:
neighbor ${ peer_group } local-as ${ config["router_bgp"]["peer_groups"][peer_group]["local_as"] } no-prepend replace-as
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("next_hop_self") == True:
neighbor ${ peer_group } next-hop-self
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("next_hop_unchanged") == True:
neighbor ${ peer_group } next-hop-unchanged
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("update_source") is not None:
neighbor ${ peer_group } update-source ${ config["router_bgp"]["peer_groups"][peer_group]["update_source"] }
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("route_reflector_client") == True:
neighbor ${ peer_group } route-reflector-client
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("bfd") == True:
neighbor ${ peer_group } bfd
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("ebgp_multihop") is not None:
neighbor ${ peer_group } ebgp-multihop ${ config["router_bgp"]["peer_groups"][peer_group]["ebgp_multihop"] }
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("password") is not None:
neighbor ${ peer_group } password 7 ${ config["router_bgp"]["peer_groups"][peer_group]["password"] }
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("send_community") is not None and config["router_bgp"]["peer_groups"][peer_group]["send_community"] == "all":
neighbor ${ peer_group } send-community
%         elif config["router_bgp"]["peer_groups"][peer_group].get("send_community") is not None:
neighbor ${ peer_group } send-community ${ config["router_bgp"]["peer_groups"][peer_group]["send_community"] }
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("maximum_routes") is not None and config["router_bgp"]["peer_groups"][peer_group].get("maximum_routes_warning_limit") is not None:
neighbor ${ peer_group } maximum-routes ${ config["router_bgp"]["peer_groups"][peer_group]["maximum_routes"] } warning-limit ${ config["router_bgp"]["peer_groups"][peer_group]["maximum_routes_warning_limit"] }
%         elif config["router_bgp"]["peer_groups"][peer_group].get("maximum_routes") is not None:
neighbor ${ peer_group } maximum-routes ${ config["router_bgp"]["peer_groups"][peer_group]["maximum_routes"] }
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("weight") is not None:
neighbor ${ peer_group } weight ${ config["router_bgp"]["peer_groups"][peer_group]["weight"] }
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("timers") is not None:
neighbor ${ peer_group } timers ${ config["router_bgp"]["peer_groups"][peer_group]["timers"] }
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("route_map_in") is not None:
neighbor ${ peer_group } route-map ${ config["router_bgp"]["peer_groups"][peer_group]["route_map_in"] } in
%         endif
%         if config["router_bgp"]["peer_groups"][peer_group].get("route_map_out") is not None:
neighbor ${ peer_group } route-map ${ config["router_bgp"]["peer_groups"][peer_group]["route_map_out"] } out
%         endif
%       endfor
%     endif
## {%     for neighbor_interface in router_bgp.neighbor_interfaces | arista.avd.natural_sort %}
## {%         set neighbor_interface_cli = "neighbor interface " ~ neighbor_interface %}
## {%         if router_bgp.neighbor_interfaces[neighbor_interface].peer_group is arista.avd.defined %}
## {%             set neighbor_interface_cli = neighbor_interface_cli ~ " peer-group " ~ router_bgp.neighbor_interfaces[neighbor_interface].peer_group %}
## {%         endif %}
## {%         if router_bgp.neighbor_interfaces[neighbor_interface].remote_as is arista.avd.defined %}
## {%             set neighbor_interface_cli = neighbor_interface_cli ~ " remote-as " ~ router_bgp.neighbor_interfaces[neighbor_interface].remote_as %}
## {%         endif %}
## ##    {{ neighbor_interface_cli }}
## {%     endfor %}
%     if config["router_bgp"].get("neighbors") is not None:
%       for neighbor in natural_sort(config["router_bgp"]["neighbors"].keys()):
%         if config["router_bgp"]["neighbors"][neighbor].get("peer_group") is not None:
neighbor ${ neighbor } peer group ${ config["router_bgp"]["neighbors"][neighbor]["peer_group"] }
%         endif
%         if config["router_bgp"]["neighbors"][neighbor].get("remote_as") is not None:
neighbor ${ neighbor } remote-as ${ config["router_bgp"]["neighbors"][neighbor]["remote_as"] }
%         endif
%         if config["router_bgp"]["neighbors"][neighbor].get("next_hop_self") == True:
neighbor ${ neighbor } next-hop-self
%         endif
%         if config["router_bgp"]["neighbors"][neighbor].get("shutdown") == True:
neighbor ${ neighbor } shutdown
%         endif
%         if config["router_bgp"]["neighbors"][neighbor].get("local_as") is not None:
neighbor ${ neighbor } local-as ${ config["router_bgp"]["neighbors"][neighbor]["local_as"] } no-prepend replace-as
%         endif
%         if config["router_bgp"]["neighbors"][neighbor].get("description") is not None:
neighbor ${ neighbor } description ${ config["router_bgp"]["neighbors"][neighbor]["description"] }
%         endif
%         if config["router_bgp"]["neighbors"][neighbor].get("update_source") is not None:
neighbor ${ neighbor } update-source ${ config["router_bgp"]["neighbors"][neighbor]["update_source"] }
%         endif
%         if config["router_bgp"]["neighbors"][neighbor].get("bfd") == True:
neighbor ${ neighbor } bfd
%         endif
%         if config["router_bgp"]["neighbors"][neighbor].get("password") is not None:
neighbor ${ neighbor } password 7 ${ config["router_bgp"]["neighbors"][neighbor]["password"] }
%         endif
%         if config["router_bgp"]["neighbors"][neighbor].get("weight") is not None:
neighbor ${ neighbor } weight ${ config["router_bgp"]["neighbors"][neighbor]["weight"] }
%         endif
%         if config["router_bgp"]["neighbors"][neighbor].get("timers") is not None:
neighbor ${ neighbor } timers ${ config["router_bgp"]["neighbors"][neighbor]["timers"] }
%         endif
%         if config["router_bgp"]["neighbors"][neighbor].get("route_map_in") is not None:
neighbor ${ neighbor } route-map ${ config["router_bgp"]["neighbors"][neighbor]["route_map_in"] } in
%         endif
%         if config["router_bgp"]["neighbors"][neighbor].get("route_map_out") is not None:
neighbor ${ neighbor } route-map ${ config["router_bgp"]["neighbors"][neighbor]["route_map_out"] } out
%         endif
%       endfor
%     endif
## {%     for aggregate_address in router_bgp.aggregate_addresses | arista.avd.natural_sort %}
## {%         set aggregate_address_cli = "aggregate-address " ~ aggregate_address %}
## {%         if router_bgp.aggregate_addresses[aggregate_address].as_set is arista.avd.defined(true) %}
## {%             set aggregate_address_cli = aggregate_address_cli ~ " as-set" %}
## {%         endif %}
## {%         if router_bgp.aggregate_addresses[aggregate_address].summary_only is arista.avd.defined(true) %}
## {%             set aggregate_address_cli = aggregate_address_cli ~ " summary-only" %}
## {%         endif %}
## {%         if router_bgp.aggregate_addresses[aggregate_address].attribute_map is arista.avd.defined %}
## {%             set aggregate_address_cli = aggregate_address_cli ~  " attribute-map " ~ router_bgp.aggregate_addresses[aggregate_address].attribute_map %}
## {%         endif %}
## {%         if router_bgp.aggregate_addresses[aggregate_address].match_map is arista.avd.defined %}
## {%             set aggregate_address_cli = aggregate_address_cli ~ " match-map " ~ router_bgp.aggregate_addresses[aggregate_address].match_map %}
## {%         endif %}
## {%         if router_bgp.aggregate_addresses[aggregate_address].advertise_only is arista.avd.defined(true) %}
## {%             set aggregate_address_cli = aggregate_address_cli ~ " advertise-only" %}
## {%         endif %}
##    {{ aggregate_address_cli }}
## {%     endfor %}
%     if config["router_bgp"].get("redistribute_routes") is not None:
%       for redistribute_route in config["router_bgp"]["redistribute_routes"].keys():
<%         redistribute_route_cli = "redistribute " + redistribute_route %>
%         if config["router_bgp"]["redistribute_routes"][redistribute_route].get("route_map") is not None:
<%             redistribute_route_cli = redistribute_route_cli + " route-map " + config["router_bgp"]["redistribute_routes"][redistribute_route]["route_map"] %>
%         endif
${ redistribute_route_cli }
%       endfor
%     endif
## L2VPNs - (vxlan) vlan based
%     if config["router_bgp"].get("vlans") is not None:
%       for vlan in config["router_bgp"]["vlans"]:
!
vlan ${ vlan }
%         if config["router_bgp"]["vlans"][vlan].rd is not None:
    rd ${ config["router_bgp"]["vlans"][vlan].rd }
%         endif
%         if config["router_bgp"]["vlans"][vlan].get("route_targets") is not None and config["router_bgp"]["vlans"][vlan]["route_targets"].get("both") is not None:
%             for route_target in config["router_bgp"]["vlans"][vlan]["route_targets"]["both"]:
    route-target both ${ route_target }
%             endfor
%         endif
%         if config["router_bgp"]["vlans"][vlan].get("route_targets") is not None and config["router_bgp"]["vlans"][vlan]["route_targets"].get("import") is not None:
%             for route_target in config["router_bgp"]["vlans"][vlan]["route_targets"]["import"]:
    route-target import ${ route_target }
%             endfor
%         endif
%         if config["router_bgp"]["vlans"][vlan].get("route_targets") is not None and config["router_bgp"]["vlans"][vlan]["route_targets"].get("export") is not None:
%             for route_target in config["router_bgp"]["vlans"][vlan]["route_targets"]["export"]:
    route-target export ${ route_target }
%             endfor
%         endif
%         if config["router_bgp"]["vlans"][vlan].get("redistribute_routes") is not None:
%           for redistribute_route in config["router_bgp"]["vlans"][vlan]["redistribute_routes"]:
    redistribute ${ redistribute_route }
%           endfor
%         endif
%       endfor
## vxlan vlan aware bundles
%       if config["router_bgp"].get("vlan_aware_bundles") is not None:
%         for vlan_aware_bundle in config["router_bgp"]["vlan_aware_bundles"].keys():
!
vlan-aware-bundle ${ vlan_aware_bundle }
%         if  config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle].get("rd") is not None:
    rd ${  config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle]["rd"] }
%         endif
%         if config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle].get("route_targets") is not None and config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle]["route_targets"].get("both") is not None:
%             for route_target in  config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle]["route_targets"]["both"]:
    route-target both ${ route_target }
%             endfor
%         endif
%         if config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle].get("route_targets") is not None and config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle]["route_targets"].get("import") is not None:
%             for route_target in config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle]["route_targets"]["import"]:
    route-target import ${ route_target }
%             endfor
%         endif
%         if config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle].get("route_targets") is not None and config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle]["route_targets"].get("export") is not None:
%             for route_target in  config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle]["route_targets"]["export"]:
    route-target export ${ route_target }
%             endfor
%         endif
%         if config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle].get("redistribute_routes") is not None:
%           for redistribute_route in config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle]["redistribute_routes"]:
    redistribute ${ redistribute_route }
%           endfor %}
%         endif
%         if config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle].get("vlan") is not None:
    vlan ${ config["router_bgp"]["vlan_aware_bundles"][vlan_aware_bundle]["vlan"] }
%         endif
%         endfor
%       endif
%     endif
## address families activation
## address family evpn activation ##
%     if config["router_bgp"].get("address_family_evpn") is not None:
!
address-family evpn
%         if config["router_bgp"]["address_family_evpn"].get("evpn_hostflap_detection") is not None and config["router_bgp"]["address_family_evpn"]["evpn_hostflap_detection"].get("enabled") == False:
    no host-flap detection
%         else:
%             if config["router_bgp"]["address_family_evpn"].get("evpn_hostflap_detection") is not None and config["router_bgp"]["address_family_evpn"]["evpn_hostflap_detection"].get("window") is not None:
    host-flap detection window ${ config["router_bgp"]["address_family_evpn"]["evpn_hostflap_detection"]["window"] }
%             endif
%             if config["router_bgp"]["address_family_evpn"].get("evpn_hostflap_detection") is not None and config["router_bgp"]["address_family_evpn"]["evpn_hostflap_detection"].get("threshold") is not None:
    host-flap detection threshold ${ config["router_bgp"]["address_family_evpn"]["evpn_hostflap_detection"]["threshold"] }
%             endif
%         endif
%         if config["router_bgp"]["address_family_evpn"].get("domain_identifier") is not None:
    domain identifier ${ config["router_bgp"]["address_family_evpn"]["domain_identifier"] }
%         endif
%         if config["router_bgp"]["address_family_evpn"].get("peer_groups") is not None:
%           for peer_group in natural_sort(config["router_bgp"]["address_family_evpn"]["peer_groups"].keys()):
%             if config["router_bgp"]["address_family_evpn"]["peer_groups"][peer_group].get("route_map_in") is not None:
    neighbor ${ peer_group } route-map ${ config["router_bgp"]["address_family_evpn"]["peer_groups"][peer_group]["route_map_in"] } in
%             endif
%             if config["router_bgp"]["address_family_evpn"]["peer_groups"][peer_group].get("route_map_out") is not None:
    neighbor ${ peer_group } route-map ${ config["router_bgp"]["address_family_evpn"]["peer_groups"][peer_group]["route_map_out"] } out
%             endif
%             if config["router_bgp"]["address_family_evpn"]["peer_groups"][peer_group].get("activate") == True:
    neighbor ${ peer_group } activate
%             elif config["router_bgp"]["address_family_evpn"]["peer_groups"][peer_group].get("activate") == False:
    no neighbor ${ peer_group } activate
%             endif
%           endfor
%         endif
%        endif
##Ahmad address family rt-membership activation 
%     if config["router_bgp"]["address_family_rt"].get("peer_groups") is not None:
!
address-family rt-membership
%         for peer_group in natural_sort(config["router_bgp"]["address_family_rt"]["peer_groups"].keys()):
%             if config["router_bgp"]["address_family_rt"]["peer_groups"][peer_group].get("activate"):
    neighbor ${ peer_group } activate
%             elif not config["router_bgp"]["address_family_rt"]["peer_groups"][peer_group].get("activate"):
    no neighbor ${ peer_group } activate
%             endif 
%             if config["router_bgp"]["address_family_rt"]["peer_groups"][peer_group].get("default_route_target") is not None:
%                 if config["router_bgp"]["address_family_rt"]["peer_groups"][peer_group]["default_route_target"].get("only"):
    neighbor ${ peer_group } default-route-target only
%                 else:
    neighbor ${ peer_group } default-route-target
%                 endif 
%             endif 
%             if config["router_bgp"]["address_family_rt"]["peer_groups"][peer_group].get("encoding_origin_as_omit") is not None:
    neighbor ${ peer_group } default-route-target encoding origin-as omit
%             endif 
%         endfor 
%     endif 
## address family ipv4 activation
%     if config["router_bgp"].get("address_family_ipv4") is not None:
!
address-family ipv4
%       if config["router_bgp"]["address_family_ipv4"].get("networks") is not None:
%         for network in config["router_bgp"]["address_family_ipv4"]["networks"].keys():
%             if config["router_bgp"]["address_family_ipv4"]["networks"][network].get("route_map") is not None:
    network ${ network } route-map ${ config["router_bgp"]["address_family_ipv4"]["networks"][network]["route_map"] }
%             else:
    network ${ network }
%             endif
%         endfor
%       endif
%       if config["router_bgp"]["address_family_ipv4"].get("peer_groups") is not None:
%           for peer_group in natural_sort(config["router_bgp"]["address_family_ipv4"]["peer_groups"].keys()):
%             if config["router_bgp"]["address_family_ipv4"]["peer_groups"][peer_group].get("route_map_in") is not None:
    neighbor ${ peer_group } route-map ${ config["router_bgp"]["address_family_ipv4"]["peer_groups"][peer_group]["route_map_in"] } in
%             endif
%             if config["router_bgp"]["address_family_ipv4"]["peer_groups"][peer_group].get("route_map_out") is not None:
    neighbor ${ peer_group } route-map ${ config["router_bgp"]["address_family_ipv4"]["peer_groups"][peer_group]["route_map_out"] } out
%             endif
%             if config["router_bgp"]["address_family_ipv4"]["peer_groups"][peer_group].get("prefix_list_in") is not None:
    neighbor ${ peer_group } prefix-list ${ config["router_bgp"]["address_family_ipv4"]["peer_groups"][peer_group]["prefix_list_in"] } in
%             endif
%             if config["router_bgp"]["address_family_ipv4"]["peer_groups"][peer_group].get("prefix_list_out") is not None:
    neighbor ${ peer_group } prefix-list ${ config["router_bgp"]["address_family_ipv4"]["peer_groups"][peer_group]["prefix_list_out"] } out
%             endif
%             if config["router_bgp"]["address_family_ipv4"]["peer_groups"][peer_group].get("activate") == True:
    neighbor ${ peer_group } activate
%             elif config["router_bgp"]["address_family_ipv4"]["peer_groups"][peer_group].get("activate") == False:
    no neighbor ${ peer_group } activate
%             endif
%           endfor
%       endif
%       if config["router_bgp"]["address_family_ipv4"].get("neighbors") is not None:
%           for neighbor in config["router_bgp"]["address_family_ipv4"]["neighbors"].keys():
%             if config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor].get("route_map_in") is not None:
    neighbor ${ neighbor } route-map ${ config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor]["route_map_in"] } in
%             endif
%             if config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor].get("route_map_out") is not None:
    neighbor ${ neighbor } route-map ${ config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor]["route_map_out"] } out
%             endif
%             if config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor].get("prefix_list_in") is not None:
    neighbor ${ neighbor } prefix-list ${ config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor]["prefix_list_in"] } in
%             endif
%             if config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor].get("prefix_list_out") is not None:
    neighbor ${ neighbor } prefix-list ${ config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor]["prefix_list_out"] } out
%             endif
%             if config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor].get("default_originate") is not None:
<%                 neighbor_default_originate_cli = "neighbor " + neighbor + " default-originate" %>
%                 if config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor]["default_originate"].get("route_map") is not None:
<%                     neighbor_default_originate_cli = neighbor_default_originate_cli + " route-map " + config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor]["default_originate"]["route_map"] %>
%                 endif
%                 if config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor]["default_originate"].get("always") == True:
<%                     neighbor_default_originate_cli = neighbor_default_originate_cli + " always" %>
%                 endif
    ${ neighbor_default_originate_cli }
%             endif
%             if config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor].get("activate") == True:
    neighbor ${ neighbor } activate
%             elif config["router_bgp"]["address_family_ipv4"]["neighbors"][neighbor].get("activate") == False:
    no neighbor ${ neighbor } activate
%             endif
%           endfor
%       endif
%     endif
## {# address family ipv4 multicast activation #}
## {%     if router_bgp.address_family_ipv4_multicast is arista.avd.defined %}
##    !
##    address-family ipv4 multicast
## {%         for peer_group in router_bgp.address_family_ipv4_multicast.peer_groups | arista.avd.natural_sort %}
## {%             if router_bgp.address_family_ipv4_multicast.peer_groups[peer_group].route_map_in is arista.avd.defined %}
##       neighbor ${ peer_group } route-map {{ router_bgp.address_family_ipv4_multicast.peer_groups[peer_group].route_map_in }} in
## {%             endif %}
## {%             if router_bgp.address_family_ipv4_multicast.peer_groups[peer_group].route_map_out is arista.avd.defined %}
##       neighbor ${ peer_group } route-map {{ router_bgp.address_family_ipv4_multicast.peer_groups[peer_group].route_map_out }} out
## {%             endif %}
## {%             if router_bgp.address_family_ipv4_multicast.peer_groups[peer_group].activate is arista.avd.defined(true) %}
##       neighbor ${ peer_group } activate
## {%             elif router_bgp.address_family_ipv4_multicast.peer_groups[peer_group].activate is arista.avd.defined(false) %}
##       no neighbor ${ peer_group } activate
## {%             endif %}
## {%         endfor %}
## {%         for neighbor in router_bgp.address_family_ipv4_multicast.neighbors | arista.avd.natural_sort %}
## {%             if router_bgp.address_family_ipv4_multicast.neighbors[neighbor].route_map_in is arista.avd.defined %}
##       neighbor {{ neighbor }} route-map {{ router_bgp.address_family_ipv4_multicast.neighbors[neighbor].route_map_in }} in
## {%             endif %}
## {%             if router_bgp.address_family_ipv4_multicast.neighbors[neighbor].route_map_out is arista.avd.defined %}
##       neighbor {{ neighbor }} route-map {{ router_bgp.address_family_ipv4_multicast.neighbors[neighbor].route_map_out }} out
## {%             endif %}
## {%             if router_bgp.address_family_ipv4_multicast.neighbors[neighbor].activate is arista.avd.defined(true) %}
##       neighbor {{ neighbor }} activate
## {%             elif router_bgp.address_family_ipv4_multicast.neighbors[neighbor].activate is arista.avd.defined(false) %}
##       no neighbor {{ neighbor }} activate
## {%             endif %}
## {%         endfor %}
## {%         for redistribute_route in router_bgp.address_family_ipv4_multicast.redistribute_routes | arista.avd.natural_sort %}
## {%             set redistribute_route_cli = "redistribute " ~ redistribute_route %}
## {%             if router_bgp.address_family_ipv4_multicast.redistribute_routes[redistribute_route].route_map is arista.avd.defined %}
## {%                 set redistribute_route_cli = redistribute_route_cli ~ " route-map " ~ router_bgp.address_family_ipv4_multicast.redistribute_routes[redistribute_route].route_map %}
## {%             endif %}
##       {{ redistribute_route_cli }}
## {%         endfor %}
## {%     endif %}
## {# address family ipv6 activation #}
## {%     if router_bgp.address_family_ipv6 is arista.avd.defined %}
##    !
##    address-family ipv6
## {%         for network in router_bgp.address_family_ipv6.networks | arista.avd.natural_sort %}
## {%             if router_bgp.address_family_ipv6.networks[network].route_map is arista.avd.defined %}
##       network {{ network }} route-map {{ router_bgp.address_family_ipv6.networks[network].route_map }}
## {%             else %}
##       network {{ network }}
## {%             endif %}
## {%         endfor %}
## {%         for peer_group in router_bgp.address_family_ipv6.peer_groups | arista.avd.natural_sort %}
## {%             if router_bgp.address_family_ipv6.peer_groups[peer_group].route_map_in is arista.avd.defined %}
##       neighbor ${ peer_group } route-map {{ router_bgp.address_family_ipv6.peer_groups[peer_group].route_map_in }} in
## {%             endif %}
## {%             if router_bgp.address_family_ipv6.peer_groups[peer_group].route_map_out is arista.avd.defined %}
##       neighbor ${ peer_group } route-map {{ router_bgp.address_family_ipv6.peer_groups[peer_group].route_map_out }} out
## {%             endif %}
## {%             if router_bgp.address_family_ipv6.peer_groups[peer_group].activate is arista.avd.defined(true) %}
##       neighbor ${ peer_group } activate
## {%             elif router_bgp.address_family_ipv6.peer_groups[peer_group].activate is arista.avd.defined(false) %}
##       no neighbor ${ peer_group } activate
## {%             endif %}
## {%         endfor %}
## {%         for neighbor in router_bgp.address_family_ipv6.neighbors | arista.avd.natural_sort %}
## {%             if router_bgp.address_family_ipv6.neighbors[neighbor].route_map_in is arista.avd.defined %}
##       neighbor {{ neighbor }} route-map {{ router_bgp.address_family_ipv6.neighbors[neighbor].route_map_in }} in
## {%             endif %}
## {%             if router_bgp.address_family_ipv6.neighbors[neighbor].route_map_out is arista.avd.defined %}
##       neighbor {{ neighbor }} route-map {{ router_bgp.address_family_ipv6.neighbors[neighbor].route_map_out }} out
## {%             endif %}
## {%             if router_bgp.address_family_ipv6.neighbors[neighbor].activate is arista.avd.defined(true) %}
##       neighbor {{ neighbor }} activate
## {%             elif router_bgp.address_family_ipv6.neighbors[neighbor].activate is arista.avd.defined(false) %}
##       no neighbor {{ neighbor }} activate
## {%             endif %}
## {%         endfor %}
## {%         for redistribute_route in router_bgp.address_family_ipv6.redistribute_routes | arista.avd.natural_sort %}
## {%             set redistribute_route_cli = "redistribute " ~ redistribute_route %}
## {%             if router_bgp.address_family_ipv6.redistribute_routes[redistribute_route].route_map is arista.avd.defined %}
## {%                 set redistribute_route_cli = redistribute_route_cl ~ " route-map " ~ router_bgp.address_family_ipv6.redistribute_routes[redistribute_route].route_map %}
## {%             endif %}
##       {{ redistribute_route_cli }}
## {%         endfor %}
## {%     endif %}
## {# address family vpn-ipv4 activation #}
## {%     if router_bgp.address_family_vpn_ipv4 is arista.avd.defined %}
##    !
##    address-family vpn-ipv4
## {%         if router_bgp.address_family_vpn_ipv4.domain_identifier is arista.avd.defined %}
##       domain identifier {{ router_bgp.address_family_vpn_ipv4.domain_identifier }}
## {%         endif %}
## {%         for peer_group in router_bgp.address_family_vpn_ipv4.peer_groups | arista.avd.natural_sort %}
## {%             if router_bgp.address_family_vpn_ipv4.peer_groups[peer_group].activate is arista.avd.defined(true) %}
##       neighbor ${ peer_group } activate
## {%             elif router_bgp.address_family_vpn_ipv4.peer_groups[peer_group].activate is arista.avd.defined(false) %}
##       no neighbor ${ peer_group } activate
## {%             endif %}
## {%         endfor %}
## {%         for neighbor in router_bgp.address_family_vpn_ipv4.neighbors | arista.avd.natural_sort %}
## {%             if router_bgp.address_family_vpn_ipv4.neighbors[neighbor].activate is arista.avd.defined(true) %}
##       neighbor {{ neighbor }} activate
## {%             elif router_bgp.address_family_vpn_ipv4.neighbors[neighbor].activate is arista.avd.defined(false) %}
##       no neighbor {{ neighbor }} activate
## {%             endif %}
## {%         endfor %}
## {%         if router_bgp.address_family_vpn_ipv4.neighbor_default_encapsulation_mpls_next_hop_self.source_interface is arista.avd.defined %}
##       neighbor default encapsulation mpls next-hop-self source-interface {{ router_bgp.address_family_vpn_ipv4.neighbor_default_encapsulation_mpls_next_hop_self.source_interface }}
## {%         endif %}
## {%     endif %}
## L3VPNs - (vxlan) VRFs
%     if config["router_bgp"].get("vrfs") is not None:
%       for vrf in config["router_bgp"]["vrfs"].keys():
!
vrf ${ vrf }
%         if config["router_bgp"]["vrfs"][vrf].get("rd") is not None:
    rd ${ config["router_bgp"]["vrfs"][vrf]["rd"] }
%         endif
%         if config["router_bgp"]["vrfs"][vrf].get("route_targets") is not None and config["router_bgp"]["vrfs"][vrf]["route_targets"].get("import") is not None:
%             for address_family in config["router_bgp"]["vrfs"][vrf]["route_targets"]["import"].keys():
%                 for route_target in config["router_bgp"]["vrfs"][vrf]["route_targets"]["import"][address_family]:
    route-target import ${ address_family } ${ route_target }
%                 endfor
%             endfor
%         endif
%         if config["router_bgp"]["vrfs"][vrf].get("route_targets") is not None and config["router_bgp"]["vrfs"][vrf]["route_targets"].get("export") is not None:
%             for address_family in config["router_bgp"]["vrfs"][vrf]["route_targets"]["export"].keys():
%                 for route_target in config["router_bgp"]["vrfs"][vrf]["route_targets"]["export"][address_family]:
    route-target export ${ address_family } ${ route_target }
%                 endfor
%             endfor
%         endif
%         if config["router_bgp"]["vrfs"][vrf].get("router_id") is not None:
    router-id ${ config["router_bgp"]["vrfs"][vrf]["router_id"] }
%         endif
%         if config["router_bgp"]["vrfs"][vrf].get("timers") is not None:
    timers bgp ${ config["router_bgp"]["vrfs"][vrf]["timers"] }
%         endif
%         if config["router_bgp"]["vrfs"][vrf].get("networks") is not None:
%           for network in config["router_bgp"]["vrfs"][vrf]["networks"].keys():
%             if config["router_bgp"]["vrfs"][vrf].networks[network].get("route_map") is not None:
    network ${ network } route-map ${ config["router_bgp"]["vrfs"][vrf]["networks"][network]["route_map"] }
%             else:
    network ${ network }
%             endif
%           endfor
%         endif
%         if config["router_bgp"]["vrfs"][vrf].get("neighbors") is not None:
%           for neighbor in config["router_bgp"]["vrfs"][vrf]["neighbors"].keys():
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("remote_as") is not None:
    neighbor ${ neighbor } remote-as ${ config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["remote_as"] }
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("peer_group") is not None:
    neighbor ${ neighbor } peer group ${ config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["peer_group"] }
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("password") is not None:
    neighbor ${ neighbor } password 7 ${ config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["password"] }
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("local_as") is not None:
    neighbor ${ neighbor } local-as ${ config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["local_as"] } no-prepend replace-as
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("description") is not None:
    neighbor ${ neighbor } description ${ config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["description"] }
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("ebgp_multihop") is not None:
<%                 neighbor_ebgp_multihop_cli = "neighbor " + neighbor + " ebgp-multihop" %>
%                 if type(config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["ebgp_multihop"]) is int:
<%                     neighbor_ebgp_multihop_cli = neighbor_ebgp_multihop_cli + " " + config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["ebgp_multihop"] %>
%                 endif
    ${ neighbor_ebgp_multihop_cli }
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("next_hop_self") == True:
    neighbor ${ neighbor } next-hop-self
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("timers") is not None:
    neighbor ${ neighbor } timers ${ config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["timers"] }
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("send_community") is not None and config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["send_community"] == "all":
    neighbor ${ neighbor } send-community
%             elif config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("send_community") is not None:
    neighbor ${ neighbor } send-community ${ config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["send_community"] }
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("maximum_routes") is not None:
    neighbor ${ neighbor } maximum-routes ${ config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["maximum_routes"] }
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("default_originate") is not None:
<%                neighbor_default_originate_cli = "neighbor " + neighbor + " default-originate" %>
%                 if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["default_originate"].get("route_map") is not None:
<%                    neighbor_default_originate_cli = neighbor_default_originate_cli + " route-map " + config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["default_originate"]["route_map"] %>
%                 endif
%                 if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["default_originate"].get("always") == True:
<%                    neighbor_default_originate_cli = neighbor_default_originate_cli+ " always" %>
%                 endif
    ${ neighbor_default_originate_cli }
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("update_source") is not None:
    neighbor ${ neighbor } update-source ${ config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["update_source"] }
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("route_map_out") is not None:
    neighbor ${ neighbor } route-map ${ config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["route_map_out"] } out
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor].get("route_map_in") is not None:
    neighbor ${ neighbor } route-map ${ config["router_bgp"]["vrfs"][vrf]["neighbors"][neighbor]["route_map_in"] } in
%             endif
%           endfor
%         endif
%         if config["router_bgp"]["vrfs"][vrf].get("redistribute_routes") is not None:
%           for redistribute_route in config["router_bgp"]["vrfs"][vrf]["redistribute_routes"].keys():
<%             redistribute_cli = "redistribute " + redistribute_route %>
%              if config["router_bgp"]["vrfs"][vrf]["redistribute_routes"][redistribute_route].get("route_map") is not None:
<%                 redistribute_cli = redistribute_cli + " route-map " + config["router_bgp"]["vrfs"][vrf]["redistribute_routes"][redistribute_route]["route_map"] %>
%              endif
    ${ redistribute_cli }
%           endfor
%         endif
%         if config["router_bgp"]["vrfs"][vrf].get("aggregate_addresses") is not None:
%           for aggregate_address in config["router_bgp"]["vrfs"][vrf]["aggregate_addresses"].keys():
<%             aggregate_address_cli = "aggregate-address " + aggregate_address %>
%             if config["router_bgp"]["vrfs"][vrf]["aggregate_addresses"][aggregate_address].get("as_set") == True:
<%                 aggregate_address_cli = aggregate_address_cli + " as-set" %>
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["aggregate_addresses"][aggregate_address].get("summary_only") == True:
<%                  aggregate_address_cli = aggregate_address_cli + " summary-only" %>
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["aggregate_addresses"][aggregate_address].get("attribute_map") is not None:
<%                  aggregate_address_cli = aggregate_address_cli + " attribute-map " + config["router_bgp"]["vrfs"][vrf]["aggregate_addresses"][aggregate_address]["attribute_map"] %>
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["aggregate_addresses"][aggregate_address].get("match_map") is not None:
<%                  aggregate_address_cli = aggregate_address_cli + " match-map " + config["router_bgp"]["vrfs"][vrf]["aggregate_addresses"][aggregate_address]["match_map"] %>
%             endif
%             if config["router_bgp"]["vrfs"][vrf]["aggregate_addresses"][aggregate_address].get("advertise_only") == True:
<%                 aggregate_address_cli = aggregate_address_cli + " advertise-only" %>
%             endif
    ${ aggregate_address_cli }
%           endfor
%         endif
%         if config["router_bgp"]["vrfs"][vrf].get("address_families") is not None:
%           for  address_family in config["router_bgp"]["vrfs"][vrf]["address_families"].keys():
    !
    address-family ${ address_family }
%             for neighbor in config["router_bgp"]["vrfs"][vrf]["address_families"][address_family]["neighbors"].keys():
%                 if config["router_bgp"]["vrfs"][vrf]["address_families"][address_family]["neighbors"][neighbor].get("activate") == True:
        neighbor ${ neighbor } activate
%                 endif
%             endfor
%             for network in config["router_bgp"]["vrfs"][vrf]["address_families"][address_family]["networks"].keys():
<%                network_cli = "network " + network %>
%                 if config["router_bgp"]["vrfs"][vrf]["address_families"][address_family]["networks"][network].get("route_map") is not None:
<%                     network_cli = network_cli + " route-map " + config["router_bgp"]["vrfs"][vrf]["address_families"][address_family]["networks"][network]["route_map"] %>
%                 endif
        ${ network_cli }
%             endfor
%           endfor
%         endif
%       endfor
%     endif
!
%     if config["router_bgp"].get('bgp_defaults'):
%       for bgp_default in config["router_bgp"]["bgp_defaults"]:
${ bgp_default }
%       endfor
!
%     endif
% endif
% endif
## router ospf
%if config.get("router_ospf") and config["router_ospf"].get("process_ids"):
%for process_id in config["router_ospf"]["process_ids"].keys():
%     if config["router_ospf"]["process_ids"][process_id].get("vrf"):
router ospf ${ process_id } vrf ${ config["router_ospf"]["process_ids"][process_id]["vrf"] }
%     else:
router ospf ${ process_id }
%     endif
%     if config["router_ospf"]["process_ids"].get("log_adjacency_changes_detail"):
log-adjacency-changes detail
%     endif
%     if config["router_ospf"]["process_ids"][process_id].get("router_id"):
router-id ${ config["router_ospf"]["process_ids"][process_id]["router_id"] }
%     endif
%     if config["router_ospf"]["process_ids"][process_id].get("passive_interface_default"):
passive-interface default
%     endif
%     if config["router_ospf"]["process_ids"][process_id].get("no_passive_interfaces"):
%         for interface in config["router_ospf"]["process_ids"][process_id]["no_passive_interfaces"]:
no passive-interface ${ interface }
%         endfor
%     endif
%     if config["router_ospf"]["process_ids"][process_id].get("network_prefixes"):
%         for network_prefix in natural_sort(config["router_ospf"]["process_ids"][process_id]["network_prefixes"].keys()):
network ${ network_prefix } area ${ config["router_ospf"]["process_ids"][process_id]["network_prefixes"][network_prefix]["area"] }
%         endfor
%     endif
%     if config["router_ospf"]["process_ids"][process_id].get("bfd_enable"):
bfd default
%     endif
%     if config["router_ospf"]["process_ids"][process_id].get("ospf_defaults"):
%         for ospf_default in config["router_ospf"]["process_ids"][process_id]["ospf_defaults"]:
${ospf_default}
%         endfor
%     endif
!
%endfor
%endif
##Ahmad eos - platform 
% if config.get("platform_settings") is not None:
!
%     if config["platform_settings"]["trident"]["forwarding_table_partition"] is not None:
platform trident forwarding-table partition ${config["platform_settings"]["trident"]["forwarding_table_partition"]}
%     endif
##{%     if platform.sand is arista.avd.defined %}
##{%         for qos_map in platform.sand.qos_maps | arista.avd.natural_sort('traffic_class') %}
##{%             if qos_map.traffic_class is arista.avd.defined and qos_map.to_network_qos is arista.avd.defined %}
##platform sand qos map traffic-class {{ qos_map.traffic_class }} to network-qos {{ qos_map.to_network_qos }}
##{%             endif %}
##{%         endfor %}
##{%         if platform.sand.lag.hardware_only is arista.avd.defined(true) %}
##platform sand lag hardware-only
##{%         endif %}
##{%         if platform.sand.lag.mode is arista.avd.defined %}
##platform sand lag mode {{ platform.sand.lag.mode }}
##{%         endif %}
##{%         if platform.sand.forwarding_mode is arista.avd.defined %}
##platform sand forwarding mode {{ platform.sand.forwarding_mode }}
##{%         endif %}
##{%         if platform.sand.multicast_replication.default is arista.avd.defined %}
##platform sand multicast replication default {{ platform.sand.multicast_replication.default }}
##{%         endif %}
##{%     endif %}
% endif
## EOS CLI
% if config.get("eos_cli"):
%   for line in config["eos_cli"]:
${line}
%   endfor
% endif
