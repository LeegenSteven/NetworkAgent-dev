# Build the Network Operator

To build and push the network operator image, run the following commands in the __NetworkAgent/operator__ directory

```
gcloud auth configure-docker $GOOGLE_REGION-docker.pkg.dev
```

To build locally and push, run the following commands from the __NetworkAgent/operator__ directory

```
docker build . -t $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networkoperator:latest
docker push $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networkoperator:latest
```

# Deploy the CRDs & Operator

Deploy the resource and service CRDs. In the __NetworkAgent/operator__ directory run the following commands

```
kubectl apply -f config
```

Update the __deployment.yaml__ manifest with your PROJECT, REGION and ZONE details as follows: 

```
  containers:
  - name: networkoperator
    image: <YOUR REGION>-docker.pkg.dev/<YOUR PROJECT>/networkagent/networkoperator:latest
    imagePullPolicy: Always
    env:
    - name: PROJECT
      value: <<YOUR PROJECT>>
    - name: REGION
      value: <<YOUR REGION>>
    - name: ZONE
      value: <<YOUR ZONE>>
```

Deploy the operator, from the __NetworkAgent/operator__ directory run the following commands. 

```
kubectl apply -f deployment.yaml
```
# Running the operator locally on your laptop

Ensure the GOOGLE_PROJECT, GOOGLE_REGION and GOOGLE_ZONE environment variables are set (as described in the initial GCP setup readme)

Run the following to start the operator on your laptop. 

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd src
kopf run main.py --verbose
```