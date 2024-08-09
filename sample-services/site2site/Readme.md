# Run a Sample Connectivity Service

Bring up site1

```
cd site1
kubectl -f network.yaml compute.yaml edgeappliance.yaml
```

Bring up site2

```
cd site2
kubectl -f network.yaml compute.yaml edgeappliance.yaml
```

Check all is running

Run IT traffic