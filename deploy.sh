#/usr/bin/bash
cd operator
docker build . -t $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networkoperator:latest
docker push $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networkoperator:latest
cd ..

kubectl delete -f operator/deployment.yaml
kubectl apply -f operator/deployment.yaml
kubectl get pods