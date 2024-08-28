# Prometheus Monitor

Start 

```
kubectl apply -f environment/prometheus.yaml
```

gcloud compute ssh monitor --zone=$GOOGLE_ZONE --tunnel-through-iap --project=$GOOGLE_PROJECT

```
gcloud compute start-iap-tunnel monitor 3000 --local-host-port=localhost:3000 --zone=$GOOGLE_ZONE
```


open a browser at http://127.0.0.1:3000

