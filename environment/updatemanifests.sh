#/usr/bin/bash

# test if gcloud exists
if ! command -v gcloud &> /dev/null
then
    echo "gcloud could not be found, you must install it"
    exit 1
fi

# test if jinja exists
if ! command -v jinja &> /dev/null
then
    echo "jinja could not be found, you must install 'jinja-cli'"
    exit 1
fi

# Test if google compute ssh keys exist, it not generate them
if ! test -f google-compute; then
  echo "SSH key google-compute does not exist, generating new keys...\n\n"
  ssh-keygen -o -a 100 -t ed25519 -f google-compute -C networkagent -P ""
fi

echo "Found google-compute ssh keys, copying where needed"
cp google-compute ../operator

if [ -z "${GOOGLE_PROJECT}" ] || [ -z "${GOOGLE_REGION}" ] || [ -z "${GOOGLE_ZONE}" ] || [ -z "${GOOGLE_USER}" ]; then
    echo "You must set GOOGLE_USER, GOOGLE_PROJECT, GOOGLE_REGION, and GOOGLE_ZONE environment variables"
    exit 0
fi

echo "Setting project to $GOOGLE_PROJECT"
gcloud config set project $GOOGLE_PROJECT

# Need to turn off the policy that forbids service key creation for this project
echo "TURN OFF POLICY THAT FORBIDS SERVICE KEY CREATION"

echo "templating k8s manifest files"
# grab the public ssh key for templating into VM manifests
export GOOGLE_SSH_KEY=$(cat ./google-compute.pub)

# check if SERVICE ACCOUNT doesnt exist
export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter=name:"networkagent@"`

# Create the service account if needed
gcloud iam service-accounts create networkagent --description="Network Agent Service Account" --display-name="Network Agent"

gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/owner"
gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/container.admin"
gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/compute.admin"
gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/compute.networkAdmin"
gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/iam.serviceAccountAdmin"
gcloud iam service-accounts keys create "./networkagent.json" --iam-account=$GOOGLE_SERVICE_ACCOUNT
cp ./network.json ../tools
cp ./network.json ../operator
cp ./network.json ../networkagent

echo "generating monitoring and configconnector yaml files"
jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE monitoring.j2 >  monitoring.yaml
jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE configconnector.j2 > configconnector.yaml

echo "generating networkagent, tools and operator yaml files"
jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../operator/deployment.j2 > ../operator/deployment.yaml
jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../tools/deployment.j2 > ../tools/deployment.yaml
jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../networkagent/deployment.j2 > ../networkagent/deployment.yaml

echo "generating customer site files"
jinja -E GOOGLE_USER -E GOOGLE_SSH_KEY -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../sample-services/customersites/london.j2 > ../sample-services/customersites/london.yaml
jinja -E GOOGLE_USER -E GOOGLE_SSH_KEY -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../sample-services/customersites/sydney.j2 > ../sample-services/customersites/sydney.yaml
jinja -E GOOGLE_USER -E GOOGLE_SSH_KEY -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../sample-services/customersites/singapore.j2 > ../sample-services/customersites/singapore.yaml
jinja -E GOOGLE_USER -E GOOGLE_SSH_KEY -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../sample-services/customersites/newyork.j2 > ../sample-services/customersites/newyork.yaml

