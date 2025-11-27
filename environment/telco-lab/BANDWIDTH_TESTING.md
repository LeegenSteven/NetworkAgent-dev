# Bandwidth Enforcement Testing Guide

This guide demonstrates how to test and verify the traffic control (tc) based bandwidth enforcement implemented in the Network Trust Engine.

## Features Implemented

### Scenario 1: Network-Level Bandwidth Enforcement
- Applies tc rules to Linux bridge interfaces
- Models physical network segment capacity
- All traffic on the network segment shares the configured bandwidth

### Scenario 2: Per-Interface Bandwidth Enforcement  
- Applies tc rules to individual veth interfaces
- Models physical port capacity on router interfaces
- Each router interface has its own bandwidth limit

## Configuration Examples

### Network-Level Bandwidth (Scenario 1)

```yaml
networks:
  - name: slow-link
    subnet: "10.1.1.0/30"
    network_type: "p2p"
    bandwidth: "100mbit"  # Entire network segment limited to 100Mbit
    connected_routers:
      - router_name: "r1"
        interface: "eth1"
        ip_address: "10.1.1.1"
      - router_name: "r2"
        interface: "eth1"
        ip_address: "10.1.1.2"
```

### Per-Interface Bandwidth (Scenario 2)

```yaml
routers:
  - name: "r1"
    interfaces:
      - name: "eth1"
        network: "slow-link"
        bandwidth: "50mbit"  # This specific interface limited to 50Mbit
```

### Combined (Hybrid) Approach

```yaml
networks:
  - name: backbone
    subnet: "10.0.0.0/30"
    bandwidth: "1gbit"  # Network capacity is 1Gbit
    connected_routers:
      - router_name: "r1"
        interface: "eth1"
        ip_address: "10.0.0.1"
      - router_name: "r2"
        interface: "eth1"
        ip_address: "10.0.0.2"

routers:
  - name: "r1"
    interfaces:
      - name: "eth1"
        network: "backbone"
        bandwidth: "500mbit"  # Port capacity is 500Mbit
```

In this hybrid example:
- The network segment has 1Gbit total capacity
- R1's port is limited to 500Mbit
- R2's port (if not specified) can use up to the network capacity
- Both limits are enforced: the stricter one applies

## Deployment

Deploy the test network:

```bash
cd /Users/briannaughton/trustengine
kubectl apply -f telco-lab/bandwidth-test.yaml
```

Wait for all resources to be ready:

```bash
# Watch VyOSNetwork status
kubectl get vyosnetwork bandwidth-test-network -w

# Check LinuxNetworks
kubectl get linuxnetwork

# Check VyOSRouters
kubectl get vyosrouter
```

## Verification Commands

### 1. Verify TC Rules on Network Bridges (Scenario 1)

Check that tc rules are applied to the bridge interfaces:

```bash
# List all bridge interfaces
brctl show

# Check tc rules on slow-link bridge
sudo tc qdisc show dev slow-link
sudo tc class show dev slow-link

# Expected output:
# qdisc htb 1: root refcnt 2 r2q 10 default 10 direct_packets_stat 0
# class htb 1:1 root rate 100Mbit ceil 100Mbit burst 1600b cburst 1600b
# class htb 1:10 parent 1:1 leaf 10: prio 0 rate 100Mbit ceil 100Mbit burst 1600b cburst 1600b

# Check fast-backbone bridge
sudo tc qdisc show dev fast-backbone
sudo tc class show dev fast-backbone

# Should show 1gbit rate limit
```

### 2. Verify TC Rules on Veth Interfaces (Scenario 2)

Check that tc rules are applied to the veth pairs:

```bash
# Find veth interfaces for a router
ip link | grep r1

# Example output: r1-eth1@if... (host side of veth pair)

# Check tc rules on specific veth
sudo tc qdisc show dev r1-eth1
sudo tc class show dev r1-eth1

# Expected output for r1-eth1:
# qdisc htb 1: root refcnt 2 r2q 10 default 10 direct_packets_stat 0
# class htb 1:1 root rate 50Mbit ceil 50Mbit burst 1600b cburst 1600b
# class htb 1:10 parent 1:1 leaf 10: prio 0 rate 50Mbit ceil 50Mbit burst 1600b cburst 1600b

# Check all veth interfaces for router r1
for iface in $(ip link | grep "r1-" | cut -d: -f2 | tr -d ' ' | cut -d@ -f1); do
    echo "=== $iface ==="
    sudo tc qdisc show dev $iface
done
```

### 3. Verify from Inside Router Container

Check that interfaces are up inside the router:

```bash
# Enter router container
docker exec -it r1 vbash

# Check interface status
show interfaces

# Check IP addresses
show interfaces detail

# Exit container
exit
```

### 4. Performance Testing with iperf3

Install iperf3 in the router containers if not already present:

```bash
# On R2, start iperf3 server
docker exec -d r2 sh -c "apt-get update && apt-get install -y iperf3 && iperf3 -s"

# From R1, test slow-link (should max at ~50Mbit due to interface limit)
docker exec r1 sh -c "apt-get update && apt-get install -y iperf3 && iperf3 -c 10.1.1.2 -t 10 -i 1"

# Expected: ~50 Mbit/sec (limited by r1-eth1 interface bandwidth)

# Test fast-backbone (should max at ~500Mbit due to interface limit)
docker exec r1 iperf3 -c 10.2.1.2 -t 10 -i 1

# Expected: ~500 Mbit/sec (limited by r1-eth2 interface bandwidth)
```

### 5. Parallel Testing to Verify Network-Level Limits

Test network-level enforcement by running traffic from multiple sources:

```bash
# If you have multiple routers on the same network
# Start iperf3 servers on both sides
docker exec -d r2 iperf3 -s -p 5201
docker exec -d r2 iperf3 -s -p 5202

# Run two parallel tests from r1
docker exec -d r1 iperf3 -c 10.1.1.2 -p 5201 -t 30 &
docker exec r1 iperf3 -c 10.1.1.2 -p 5202 -t 30

# Combined throughput should not exceed network bandwidth (100mbit for slow-link)
```

### 6. Monitor TC Statistics

Watch real-time statistics:

```bash
# Monitor tc statistics on bridge
watch -n 1 "sudo tc -s class show dev slow-link"

# Monitor tc statistics on veth
watch -n 1 "sudo tc -s class show dev r1-eth1"

# Look for:
# - Sent bytes/packets
# - Dropped packets (indicates bandwidth limit being hit)
# - Overlimits (number of times limit was exceeded)
```

### 7. Detailed TC Class Statistics

```bash
# Get detailed statistics including drops and overlimits
sudo tc -s -d class show dev slow-link

# Output includes:
# - rate: configured rate
# - Sent: total bytes/packets sent
# - dropped: packets dropped due to rate limiting
# - overlimits: times bandwidth limit was exceeded
# - backlog: queued packets
```

## Traffic Patterns to Test

### 1. Single Flow Test
```bash
docker exec r1 iperf3 -c 10.1.1.2 -t 30
# Should respect the bandwidth limit
```

### 2. UDP Test (Shows Packet Loss)
```bash
docker exec r1 iperf3 -c 10.1.1.2 -u -b 150M -t 30
# Try to send 150Mbit on 100Mbit link
# Will show packet loss due to bandwidth constraint
```

### 3. Multiple Parallel TCP Flows
```bash
docker exec r1 iperf3 -c 10.1.1.2 -P 4 -t 30
# 4 parallel connections should fairly share bandwidth
```

### 4. Bidirectional Test
```bash
docker exec r1 iperf3 -c 10.1.1.2 -d -t 30
# Tests both directions simultaneously
```

## Expected Behavior

### Network-Level Enforcement (Scenario 1)
- All traffic on the network segment is limited
- Multiple routers on the same segment share the bandwidth
- Traffic is queued and shaped at the bridge level

### Per-Interface Enforcement (Scenario 2)
- Each router interface has independent limit
- Traffic is shaped on the host side of the veth pair
- Different interfaces on same router can have different limits

### Combined Enforcement
- Both limits apply: the more restrictive one takes effect
- If interface bandwidth > network bandwidth: network limit applies
- If interface bandwidth < network bandwidth: interface limit applies

## Cleanup

Remove the test network:

```bash
kubectl delete vyosnetwork bandwidth-test-network

# Verify all resources are deleted
kubectl get linuxnetwork
kubectl get vyosrouter
kubectl get cpe
```

## Troubleshooting

### TC Rules Not Applied

Check Ansible playbook logs:
```bash
# View operator logs
kubectl logs -n default deployment/trustengine-operator -f | grep -i "bandwidth\|tc"
```

### Interface Not Rate Limited

Verify interface configuration:
```bash
# Check VyOSRouter spec
kubectl get vyosrouter r1 -o yaml | grep -A 5 bandwidth

# Check actual tc rules
sudo tc qdisc show dev r1-eth1
```

### Performance Not Matching Limits

1. Check if multiple limits are applied (network + interface)
2. Verify MTU settings (affects burst rate)
3. Check for CPU throttling on host
4. Ensure no other QoS/tc rules are interfering

```bash
# Check all tc rules on host
sudo tc qdisc show

# Check for conflicting rules
sudo iptables -t mangle -L -v -n
```

## Advanced Topics

### Customizing TC Parameters

The current implementation uses:
- **HTB (Hierarchical Token Bucket)**: For rate limiting
- **SFQ (Stochastic Fair Queueing)**: For fair bandwidth sharing among flows
- **Default burst**: Automatically calculated by tc

To customize, modify the Ansible playbooks:
- `operator/src/linuxnetwork/playbooks/create_network.yaml` (network-level)
- `operator/src/vyosrouter/playbooks/router_management.yaml` (interface-level)

### Adding Latency/Jitter/Loss

Use netem qdisc for network impairment testing:

```bash
# Add 10ms latency to a veth interface
sudo tc qdisc add dev r1-eth1 parent 1:10 handle 20: netem delay 10ms

# Add 1% packet loss
sudo tc qdisc add dev r1-eth1 parent 1:10 handle 20: netem loss 1%

# Combine delay and jitter
sudo tc qdisc add dev r1-eth1 parent 1:10 handle 20: netem delay 10ms 2ms
```

### Monitoring Bandwidth Usage

Export tc statistics to InfluxDB for visualization:

```bash
# Script to collect and export tc stats (example)
while true; do
    tc -s -j class show dev slow-link | \
    jq -r '.[] | "bandwidth,device=slow-link,class=\(.class) \
           bytes=\(.bytes),packets=\(.packets),dropped=\(.dropped)"' | \
    curl -i -XPOST "http://influxdb:8086/write?db=network" --data-binary @-
    sleep 10
done
```

## Notes

- TC rules persist until interfaces are deleted or system reboot
- Bandwidth values use SI units (1kbit = 1000 bits, 1mbit = 1,000,000 bits)
- HTB allows bursting slightly above configured rate for short periods
- SFQ ensures fair sharing among concurrent flows
- Host-side veth limits are applied - traffic from router to network is shaped
