#/usr/bin/bash
docker build . -t $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networkoperator:latest
docker push $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networkoperator:latest

kubectl delete -f deployment.yaml
kubectl apply -f deployment.yaml
kubectl get pods