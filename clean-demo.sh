#!/usr/bin/bash
#
# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Get all PTP object names in the vpn namespace
PTP_NAMES=$(kubectl get ptp -n vpn -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
echo "Found PTP objects:"
echo "$PTP_NAMES"
if [ -n "$PTP_NAMES" ]; then
  for ptp_name in $PTP_NAMES; do
    kubectl delete ptp "$ptp_name" -n vpn
    if [ $? -eq 0 ]; then
      echo "Successfully deleted PTP: $ptp_name"
    else
      echo "Failed to delete PTP: $ptp_name"
    fi
  done
fi

# Get all MESH object names in the vpn namespace
MESH_NAMES=$(kubectl get mesh -n vpn -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
echo "Found Mesh objects:"
echo "$MESH_NAMES"
if [ -n "$MESH_NAMES" ]; then
  for mesh_name in $MESH_NAMES; do
    kubectl delete mesh "$mesh_name" -n vpn
    if [ $? -eq 0 ]; then
      echo "Successfully deleted MESH: $mesh_name"
    else
      echo "Failed to delete MESH: $mesh_name"
    fi
  done
fi

UERANSIM_NAMES=$(kubectl get ueransim -n cellsite1 -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
# Get all UERANSIM object names in the cellsite1 namespace
UERANSIM_NAMES=$(kubectl get ueransim -n cellsite1 -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
echo "Found UERANSIM objects:"
echo "$UERANSIM_NAMES"
if [ -n "$UERANSIM_NAMES" ]; then
  for ue_name in $UERANSIM_NAMES; do
    kubectl delete ueransim "$ue_name" -n cellsite1
    if [ $? -eq 0 ]; then
      echo "Successfully deleted UERANSIM: $ue_name"
    else
      echo "Failed to delete UERANSIM: $ue_name"
    fi
  done
fi

# Delete all cnrm resources in cellsite namespaces
for namespace in cellsite1 cellsite2; do
  for kind in ComputeFirewall ComputeRoute ComputeSubnetwork ComputeInstance ComputeNetwork; do
    for resource in $(kubectl get $kind -n $namespace -o jsonpath='{.items[*].metadata.name}'); do
      kubectl delete $kind $resource -n $namespace
    done
  done
done

# bounce the operator in case of config connector issues
kubectl delete -f operator/deployment.yaml
kubectl apply -f operator/deployment.yaml