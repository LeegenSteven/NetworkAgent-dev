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

# Delete all running traffic test
# Get all PTP object names in the network namespace
UETEST_NAMES=$(kubectl get uetest -n network -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
echo "Found UE Test objects:"
echo "$UETEST_NAMES"
if [ -n "$UETEST_NAMES" ]; then
  for uetest_name in $UETEST_NAMES; do
    kubectl delete uetest "$uetest_name" -n network &
    job_id=$!
    echo "Waiting 1 min for deletion to complete"
    timeout 1m sh -c "while kill -0 $job_id 2>/dev/null; do sleep 1; done"
    if [ $? -eq 124 ]; then
      echo "Patching $uetest_name finalizers to force deletion..."
      kubectl patch uetest $uetest_name -n network --patch '{"metadata":{"finalizers":[]}}'  --type=merge
    fi
    if [ $? -eq 0 ]; then
      echo "Successfully deleted UE test: $uetest_name"
    else
      echo "Failed to delete UE test: $uetest_name"
    fi
  done
fi

# Get all PTP object names in the network namespace
PTP_NAMES=$(kubectl get ptp -n network -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
echo "Found PTP objects:"
echo "$PTP_NAMES"
if [ -n "$PTP_NAMES" ]; then
  for ptp_name in $PTP_NAMES; do
    kubectl delete ptp "$ptp_name" -n network &
    job_id=$!
    echo "Waiting up to 1 min for deletion to complete"
    timeout 1m sh -c "while kill -0 $job_id 2>/dev/null; do sleep 1; done"
    if [ $? -eq 124 ]; then
      echo "Patching $ptp_name finalizers to force deletion..."
      kubectl patch mesh $ptp_name -n network --patch '{"metadata":{"finalizers":[]}}'  --type=merge
    fi
    if [ $? -eq 0 ]; then
      echo "Successfully deleted PTP: $ptp_name"
    else
      echo "Failed to delete PTP: $ptp_name"
    fi
  done
fi

# Get all MESH object names in the network namespace
MESH_NAMES=$(kubectl get mesh -n network -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
echo "Found Mesh objects:"
echo "$MESH_NAMES"
if [ -n "$MESH_NAMES" ]; then
  for mesh_name in $MESH_NAMES; do
    kubectl delete mesh "$mesh_name" -n network &
    job_id=$!
    echo "Waiting up to 1 min for deletion to complete"
    timeout 1m sh -c "while kill -0 $job_id 2>/dev/null; do sleep 1; done"
    if [ $? -eq 124 ]; then
      echo "Patching $mesh_name finalizers to force deletion..."
      kubectl patch mesh $mesh_name -n network --patch '{"metadata":{"finalizers":[]}}'  --type=merge
    fi
    if [ $? -eq 0 ]; then
      echo "Successfully deleted MESH: $mesh_name"
    else
      echo "Failed to delete MESH: $mesh_name"
    fi
  done
fi

UERANSIM_NAMES=$(kubectl get ueransim -n network -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
# Get all UERANSIM object names in the network namespace
UERANSIM_NAMES=$(kubectl get ueransim -n network -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
echo "Found UERANSIM objects:"
echo "$UERANSIM_NAMES"
if [ -n "$UERANSIM_NAMES" ]; then
  for ue_name in $UERANSIM_NAMES; do
    kubectl delete ueransim "$ue_name" -n network &
    job_id=$!
    echo "Waiting up to 1 min for deletion to complete"
    timeout 1m sh -c "while kill -0 $job_id 2>/dev/null; do sleep 1; done"
    if [ $? -eq 124 ]; then
      echo "Patching $ue_name finalizers to force deletion..."
      kubectl patch ueransim $ue_name -n network --patch '{"metadata":{"finalizers":[]}}'  --type=merge
    fi
    if [ $? -eq 0 ]; then
      echo "Successfully deleted UERANSIM: $ue_name"
    else
      echo "Failed to delete UERANSIM: $ue_name"
    fi
  done
fi

# Delete all cell site locations
for name in cellsite1 cellsite2; do
  echo "Deleting resources of $name location... "
  for kind in ComputeFirewall ComputeSubnetwork ComputeNetwork; do
    kubectl get $kind $name -n network 2>/dev/null || continue
    # Resource still exists. Proceed with deletion
    echo "Deleting $kind $name..."
    kubectl delete $kind $name -n network 2>/dev/null &
    job_id=$!
    echo "Waiting up to 1 min for deletion to complete"
    timeout 1m sh -c "while kill -0 $job_id 2>/dev/null; do sleep 1; done"
    if [ $? -eq 124 ]; then
      echo "Patching $name finalizers to force deletion..."
      kubectl patch $kind $name -n network --patch '{"metadata":{"finalizers":[]}}'  --type=merge
    fi
  done
done

# bounce the operator in case of config connector issues
kubectl delete -f operator/deployment.yaml
kubectl apply -f operator/deployment.yaml