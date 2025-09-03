# 5G Virtual Network Operating Procedure

## Troubleshooting and Problem Resolution Guide

### Document Overview

This operating procedure provides a systematic approach to identifying, diagnosing, and resolving problems in the virtual 5G network deployed by the NetworkAgent project.

## 1. Network Architecture Overview

The network architecture contains the following components. 

- **Core Network Site**: Free5gc-based 5G Core network functions
  - Control Plane CNFs (virtual machine on core network)
  - User Plane Function (UPF) VNF (routes between core and internet networks)
  - Data Network (DNN mimicking Internet)
- **Radio Sites**: UERANSIM gNB Radio Network Simulator VNFs
- **VPN Services**: Wireguard VNFs providing mesh or point-to-point connectivity
- **GCP Networks**: Private VPCs connecting all components

## 2. Incident Workflow

For each recorded incident, follow the steps below to further investigate and attempt resolution.

### Step 1: Investigate the incident

1. **Identify Affected Components**:
   - Identify which component instance is reporting the incident. The name of the reporting node will be used to investigate further details. 

2. **Check the status of the affected component/network service infrastructure**:
   - Check the status of the affected component's infrastructure instances and also check the status of any related component network servce instances, e.g. 
      * Check the ComputeInstance status of the affected component 
      * also check the status of related ComputeNetworks, CompuetSubnetworks or ComputeAddresses.

3. **Check the status of connected components**:
   - For UE connection errors, check the status of the infrastructure for all components or network service instances between the affected radio site and target DNN.
   - For UE or Radio errors, check the Control Plane logs for any clues or indication of what could cause the error.
   - For other components, check components that are 1 hop away to see if this issue is affecting nearby connected network services.

### Step 2: Investigate Root Cause

**Required Information:**
- Error messages or logs
- Time of first occurrence
- Recent changes or deployments

### Step 3: Identify resolution

Work through the troubleshooting procedures for the root cause component to identify a potential resolution to the issue. 

## 3 Component Troubleshooting

### 3.1 Control Plane Problems

**Symptoms:**
- UE registration failures
- Session establishment failures
- Authentication errors

**Troubleshooting Steps:**
1. Use Logs Agent to analyze control plane logs
3. Verify core network connectivity by running ping tests from a radio simulator site

**Common Resolutions:**
- Restart Free5gc control plane services
- Check the UE authentication credentials attached to the UERanSIM are correct

### 3.2 Radio Site Issues

**Symptoms:**
- UE connection failures to specific sites
- Radio link establishment failures
- Site-specific performance issues

**Troubleshooting Steps:**
1. Check UERANSIM gNB is running from the logs, if gNB is not running errors will be reported in the logs
2. Verify cellsite network connectivity by running a ping test from the UERanSIM to the control plane network service

**Common Resolutions:**
- Restart UERANSIM gNB VNF

### 3.5 VPN Connectivity Issues

**Symptoms:**
- Inter-site connectivity failures
- Mesh connectivity degradation
- Point-to-point link failures

**Troubleshooting Steps:**
2. Verify wg0 network interface exists and is active by querying the network performance statistics for this wireguard appliance. 
5. Analyze VPN logs

**Common Resolutions:**
- Restart Wireguard VNFs

*This document should be reviewed and updated quarterly or after any major network changes.*
