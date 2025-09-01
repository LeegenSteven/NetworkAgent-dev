# 5G Virtual Network Operating Procedure

## Troubleshooting and Problem Resolution Guide

### Document Overview
This operating procedure provides a systematic approach to identifying, diagnosing, and resolving problems in the virtual 5G network deployed by the NetworkAgent project. The procedure leverages the automated network agents and follows established escalation paths.

---

## 1. Network Architecture Overview

### Core Components
- **Core Network Site**: Free5gc-based 5G Core network functions
  - Control Plane CNFs (virtual machine on core network)
  - User Plane Function (UPF) VNF (routes between core and internet networks)
  - Data Network (DNN mimicking Internet)
- **Radio Sites**: UERANSIM gNB Radio Network Simulator VNFs
- **VPN Services**: Wireguard VNFs providing mesh or point-to-point connectivity
- **GCP Networks**: Private VPCs connecting all components

### Network Agents Available
| Agent | Primary Function | Use Case |
|-------|------------------|----------|
| Supervisor Agent | Routes user requests to appropriate agents | Initial contact point for all issues |
| Engineering Agent | Network design and implementation planning | Complex network changes and deployments |
| Operations Agent | Network state queries and service management | Status checks and inventory |
| Resolver Agent | Fault diagnosis and auto-resolution | Automated problem resolution |
| Incident Agent | Background monitoring and issue detection | Proactive fault detection |
| Logs Agent | Log analysis and RAG queries | Historical analysis and troubleshooting |
| Tester Agent | Network testing and validation | Connectivity and performance testing |

---

## 2. Problem Classification

### Severity Levels

#### **CRITICAL (P1)**
- Complete 5G network service outage
- Core network functions down
- All UE connections failing
- Security breaches

#### **HIGH (P2)**
- Partial service degradation affecting multiple sites
- Single core network function failure with backup available
- VPN connectivity issues affecting multiple sites
- Performance degradation >50%

#### **MEDIUM (P3)**
- Single radio site issues
- Intermittent connectivity problems
- Performance degradation 20-50%
- Non-critical configuration issues

#### **LOW (P4)**
- Minor configuration inconsistencies
- Performance degradation <20%
- Documentation updates needed
- Enhancement requests

---

## 3. Initial Problem Assessment

### Step 1: Immediate Triage
1. **Determine Severity** using classification above
2. **Identify Affected Components**:
   - Core Network (Control Plane/UPF)
   - Radio Sites (gNB/UE simulators)
   - VPN Connectivity
   - Data Network connectivity
3. **Check System Status**:
   - Access dashboard for real-time status
   - Review active alerts and notifications

### Step 2: Gather Initial Information
**Required Information:**
- Problem description and symptoms
- Time of first occurrence
- Affected network locations/services
- Recent changes or deployments
- Error messages or logs

---

## 4. Automated Resolution Workflow

### Phase 1: Automated Agent Response
1. **Incident Agent Detection**
   - Background monitoring automatically detects issues
   - Triggers fault service via pub/sub
   - Initiates resolver agent investigation

2. **Resolver Agent Analysis**
   - Performs initial diagnosis
   - Attempts automated resolution
   - Escalates to human operators if needed

3. **Engineering Agent Consultation**
   - For complex issues requiring network changes
   - Requires approval for implementation
   - Provides detailed resolution plans

### Phase 2: Human-Assisted Resolution
If automated resolution fails or requires approval:

1. **Contact Supervisor Agent**
   - Use natural language to describe the problem
   - Agent routes to appropriate specialist agent
   - Provides progress updates throughout resolution

2. **Specialist Agent Interaction**
   - Operations Agent: Query current network state
   - Logs Agent: Analyze historical data and logs
   - Tester Agent: Validate connectivity and performance
   - Engineering Agent: Plan and implement changes

---

## 5. Troubleshooting Procedures by Component

### 5.1 Core Network Issues

#### Control Plane Problems
**Symptoms:**
- UE registration failures
- Session establishment failures
- Authentication errors

**Troubleshooting Steps:**
1. Query Operations Agent for core network status
2. Use Logs Agent to analyze control plane logs
3. Check Free5gc CNF status in virtual machine
4. Verify core network connectivity
5. Test with Tester Agent

**Common Resolutions:**
- Restart Free5gc control plane services
- Verify network configuration
- Check authentication credentials
- Validate PLMN configuration

#### User Plane Function (UPF) Issues
**Symptoms:**
- Data connectivity failures
- Routing problems between core and internet
- Packet loss or high latency

**Troubleshooting Steps:**
1. Check UPF VNF status
2. Verify routing between core and internet networks
3. Analyze traffic flows with Logs Agent
4. Test connectivity with Tester Agent
5. Check network interface configurations

**Common Resolutions:**
- Restart UPF VNF
- Update routing tables
- Verify network interface bindings
- Check firewall rules

### 5.2 Radio Site Issues

#### gNB Simulator Problems
**Symptoms:**
- UE connection failures to specific sites
- Radio link establishment failures
- Site-specific performance issues

**Troubleshooting Steps:**
1. Query Operations Agent for radio site status
2. Check UERANSIM gNB VNF status
3. Verify cellsite network connectivity
4. Test UE simulator connections
5. Analyze radio logs with Logs Agent

**Common Resolutions:**
- Restart UERANSIM gNB VNF
- Verify radio configuration parameters
- Check network connectivity to core
- Update radio site configuration

#### UE Simulator Issues
**Symptoms:**
- Individual UE connection failures
- Session establishment problems
- Data transfer failures

**Troubleshooting Steps:**
1. Test individual UE connections
2. Verify UE configuration parameters
3. Check authentication credentials
4. Analyze UE logs
5. Test with different UE simulators

### 5.3 VPN Connectivity Issues

#### Wireguard Tunnel Problems
**Symptoms:**
- Inter-site connectivity failures
- Mesh connectivity degradation
- Point-to-point link failures

**Troubleshooting Steps:**
1. Check Wireguard VNF status on affected sites
2. Verify tunnel configuration
3. Test connectivity between VPC endpoints
4. Check routing tables and static routes
5. Analyze VPN logs

**Common Resolutions:**
- Restart Wireguard VNFs
- Regenerate tunnel keys
- Update routing configurations
- Verify firewall rules
- Check network interface assignments

### 5.4 Network Connectivity Issues

#### GCP Network Problems
**Symptoms:**
- VPC connectivity failures
- Routing issues between networks
- DNS resolution problems

**Troubleshooting Steps:**
1. Verify GCP network status
2. Check VPC peering configurations
3. Validate subnet configurations
4. Test DNS resolution
5. Verify firewall rules

---

## 6. Escalation Procedures

### Level 1: Automated Resolution
- **Trigger**: Incident Agent detection
- **Action**: Resolver Agent attempts auto-resolution
- **Timeframe**: Immediate to 15 minutes
- **Escalation Criteria**: Auto-resolution fails or requires approval

### Level 2: Agent-Assisted Resolution
- **Trigger**: Level 1 escalation or direct user request
- **Action**: Human operator works with Supervisor Agent
- **Timeframe**: 15 minutes to 2 hours
- **Escalation Criteria**: Complex issues requiring engineering changes

### Level 3: Engineering Intervention
- **Trigger**: Level 2 escalation
- **Action**: Engineering Agent creates detailed resolution plan
- **Timeframe**: 2-8 hours
- **Escalation Criteria**: Network architecture changes required

### Level 4: Expert Consultation
- **Trigger**: Level 3 escalation
- **Action**: Involve network architects and senior engineers
- **Timeframe**: 8+ hours
- **Escalation Criteria**: Novel issues or major architectural problems

---

## 7. Communication Protocols

### Internal Communication
- **Primary**: Supervisor Agent natural language interface
- **Secondary**: Direct agent-to-agent A2A protocol communication
- **Documentation**: All actions logged in network topology database

### External Communication
- **Stakeholder Updates**: Via dashboard notifications
- **Status Reports**: Automated through incident management system
- **Escalation Notifications**: Triggered by fault service

---

## 8. Post-Resolution Procedures

### Immediate Actions
1. **Verify Resolution**
   - Use Tester Agent to validate functionality
   - Confirm all affected services restored
   - Monitor for recurring issues

2. **Update Documentation**
   - Record resolution steps in knowledge base
   - Update network topology if changes made
   - Document lessons learned

### Follow-up Actions
1. **Root Cause Analysis**
   - Use Logs Agent for historical analysis
   - Identify contributing factors
   - Recommend preventive measures

2. **Process Improvement**
   - Update automated resolution rules
   - Enhance monitoring capabilities
   - Refine escalation procedures

---

## 9. Preventive Maintenance

### Regular Health Checks
- **Daily**: Automated monitoring via Incident Agent
- **Weekly**: Comprehensive testing with Tester Agent
- **Monthly**: Full network validation and performance review

### Proactive Monitoring
- **Real-time**: Dashboard monitoring of all components
- **Trending**: Performance metrics analysis
- **Predictive**: Log analysis for early warning signs

### Configuration Management
- **Version Control**: All configurations in GitOps repository
- **Change Management**: Engineering Agent approval workflow
- **Backup**: Regular configuration snapshots

---

## 10. Emergency Procedures

### Network-Wide Outage
1. **Immediate Response**
   - Activate incident response team
   - Assess scope and impact
   - Implement emergency communication plan

2. **Recovery Actions**
   - Execute disaster recovery procedures
   - Restore from known good configurations
   - Validate service restoration

### Security Incidents
1. **Containment**
   - Isolate affected components
   - Preserve evidence
   - Notify security team

2. **Investigation**
   - Use Logs Agent for forensic analysis
   - Coordinate with security specialists
   - Document findings

---

## 11. Tools and Resources

### Agent Access Points
- **Supervisor Agent**: Primary interface for all requests
- **Dashboard**: Real-time network status and metrics
- **A2A Protocol**: Direct agent communication interface

### Network Information
- **CIDR Ranges**: Avoid 10.0.0.0/24, 10.0.100.0/24, 10.60.0.0/24
- **Reserved Locations**: dataplane network location
- **Service Constraints**: UPF and UERanSim must use different locations

### Documentation References
- Network design specifications
- Agent capability documentation
- GCP environment configuration
- Free5gc operational guides

---

## 12. Contact Information

### Primary Contacts
- **Network Operations Center**: [Contact Details]
- **Engineering Team**: [Contact Details]
- **Security Team**: [Contact Details]

### Emergency Contacts
- **On-call Engineer**: [Contact Details]
- **Network Architect**: [Contact Details]
- **Management Escalation**: [Contact Details]

---

*This document should be reviewed and updated quarterly or after any major network changes.*
