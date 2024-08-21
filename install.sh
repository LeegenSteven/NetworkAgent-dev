#/usr/bin/bash

############################################################
# Check pre-requisites                                     #
############################################################
# test if gcloud exists
if ! command -v gcloud &> /dev/null
then
    echo "gcloud could not be found, you must install it"
    exit 0
fi

# test if jinja exists
if ! command -v jinja &> /dev/null
then
    echo "jinja could not be found, you must run 'pip install jinja-cli'"
    exit 0
fi

if [ -z "${GOOGLE_PROJECT}" ] || [ -z "${GOOGLE_REGION}" ] || [ -z "${GOOGLE_ZONE}" ] || [ -z "${GOOGLE_USER}" ]; then
    echo "You must set GOOGLE_USER, GOOGLE_PROJECT, GOOGLE_REGION, and GOOGLE_ZONE environment variables"
    exit 0
fi

############################################################
# Create keys and manifest files                           #
############################################################
Create()
{
    echo "Setting project to $GOOGLE_PROJECT"
    gcloud config set project $GOOGLE_PROJECT

    # Test if google compute ssh keys exist, it not generate them
    if ! test -f google-compute; then
        echo "SSH key google-compute does not exist, generating new keys...\n\n"
        ssh-keygen -o -a 100 -t ed25519 -f google-compute -C networkagent -P ""
    fi

    echo "Found google-compute ssh keys, copying where needed"
    cp google-compute operator
    cp google-compute.pub operator

    echo "Add ssh key to OS login"
    gcloud compute os-login ssh-keys add --key-file=google-compute.pub --project=$GOOGLE_PROJECT --ttl=5d

    echo "Templating k8s manifest files"

    # grab the public ssh key for templating into VM manifests
    export GOOGLE_SSH_KEY=$(cat google-compute.pub)

    # check if SERVICE ACCOUNT exists
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter=name:"networkagent@"`

    # Create the service account if it doesnt exist
    if [ -z "${GOOGLE_SERVICE_ACCOUNT}" ]; then
        echo "No Service Account, trying to create one"
        gcloud iam service-accounts create networkagent --description="Network Agent Service Account" --display-name="Network Agent"
        # recreate the service account environment variable
        export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter=name:"networkagent@"`
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/owner"
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/container.admin"
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/compute.admin"
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/compute.networkAdmin"
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/iam.serviceAccountAdmin"
    fi

    if ! test -f networkagent.json; then
        gcloud iam service-accounts keys create "networkagent.json" --iam-account=$GOOGLE_SERVICE_ACCOUNT
    fi

    # check networkagent.json is not zero size and copy around if it is
    if [[ -s "networkagent.json"  ]]
    then
        echo "copying networkagent.json"
        cp networkagent.json tools
        cp networkagent.json operator
        cp networkagent.json networkagent
    else
        echo "networkagent.json is empty, check your project is allowed to create service account keys."
        exit 0
    fi

    echo "generating monitoring and configconnector yaml files"
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE environment/monitoring.j2 >  environment/monitoring.yaml
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE environment/configconnector.j2 > environment/configconnector.yaml

    echo "generating networwkagent, tools and operator yaml files"
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE operator/deployment.j2 > operator/deployment.yaml
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE tools/deployment.j2 > tools/deployment.yaml
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE networkagent/deployment.j2 > networkagent/deployment.yaml

    echo "generating customer site files"
    jinja -E GOOGLE_USER -E GOOGLE_SSH_KEY -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE sample-services/customersites/london.j2 > sample-services/customersites/london.yaml
    jinja -E GOOGLE_USER -E GOOGLE_SSH_KEY -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE sample-services/customersites/sydney.j2 > sample-services/customersites/sydney.yaml
    jinja -E GOOGLE_USER -E GOOGLE_SSH_KEY -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE sample-services/customersites/singapore.j2 > sample-services/customersites/singapore.yaml
    jinja -E GOOGLE_USER -E GOOGLE_SSH_KEY -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE sample-services/customersites/newyork.j2 > sample-services/customersites/newyork.yaml
}

############################################################
# Start GKE, config connector and customer sites           #
############################################################
Start()
{
    echo "Start the network agent"

    # check if SERVICE ACCOUNT exists
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter=name:"networkagent@"`

    # Create the service account if it doesnt exist
    if [ -z "${GOOGLE_SERVICE_ACCOUNT}" ]; then
        echo "Cannot find the service account - run this script with the -c option"
        exit 0
    fi
    echo "Service account = $GOOGLE_SERVICE_ACCOUNT"

    echo "Creating mgmt network"
    gcloud compute networks create mgmt --subnet-mode=custom
    gcloud compute networks subnets create mgmt-subnet --network=mgmt --range=10.0.100.0/24 --region=$GOOGLE_REGION
    gcloud compute firewall-rules create mgmt-ingress --network mgmt --allow tcp:8080,tcp:22,tcp:3389,tcp:443,icmp --direction INGRESS --source-ranges 0.0.0.0/0

    # Create the docker repo
    echo "Creating artifact repository"
    gcloud artifacts repositories create networkagent --repository-format=docker --location=$GOOGLE_REGION --description="Network Agent Repository"

    # create the GKE cluster
    echo "Creating GKE cluster - this may take 5-10 minutes"
    gcloud container clusters create networkautomation \
    --release-channel stable \
    --addons ConfigConnector \
    --service-account $GOOGLE_SERVICE_ACCOUNT\
    --scopes default,storage-full,cloud-platform,bigquery \
    --workload-pool $GOOGLE_PROJECT.svc.id.goog \
    --logging SYSTEM \
    --monitoring SYSTEM \
    --zone $GOOGLE_ZONE\
    --node-locations $GOOGLE_ZONE \
    --num-nodes 5 \
    --network mgmt \
    --subnetwork mgmt-subnet

    gcloud components install kubectl
    gcloud container clusters get-credentials networkautomation --region=$GOOGLE_ZONE

    gcloud iam service-accounts add-iam-policy-binding \
    $GOOGLE_SERVICE_ACCOUNT \
        --member="serviceAccount:$GOOGLE_PROJECT.svc.id.goog[cnrm-system/cnrm-controller-manager]" \
        --role="roles/iam.workloadIdentityUser"

    kubectl create namespace automation
    kubectl annotate namespace automation cnrm.cloud.google.com/project-id=$GOOGLE_PROJECT
    kubectl config set-context --current --namespace automation
    kubectl apply -f configconnector.yaml

    kubectl wait -n cnrm-system --for=condition=Ready pod --all
}

############################################################
# Delete GKE, config connector and customer sites          #
############################################################
Delete()
{
    echo "Deleting environment manifests and keys"
    rm operator/deployment.yaml
    rm operator/google-compute*
    rm operator/networkagent.json

    rm tools/deployment.yaml
    rm tools/networkagent.json

    rm networkagent/deployment.yaml
    rm networkagent/networkagent.json

    rm environment/monitoring.yaml
    rm environment/configconnector.yaml

    rm networkagent.json
    rm google-compute*

    echo "Delete any deployed connectivity services"

    echo "Delete customer location"

    rm sample-services/customersites/*yaml

    echo "Delete network automsation GKE"
}

############################################################
# Setup Monitoring                                         #
############################################################
Monitoring()
{
    export GOOGLE_PROJECT_NUMBER=gcloud projects describe $GOOGLE_PROJECT --format="value(projectNumber)"
    service-$GOOGLE_PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com

}

############################################################
# Help                                                     #
############################################################
Help()
{
   # Display Help
   echo "Network Agent environment manager."
   echo
   echo "Syntax: scriptTemplate [-c|-s|-d]"
   echo "options:"
   echo "  -c     create manifests."
   echo "  -s     start network agent environment."
   echo "  -d     delete the network agent environment."
   echo
}

############################################################
# Process the input options. Add options as needed.        #
############################################################
# Get the options
while getopts ":hcsd" option; do
   case $option in
      h) 
        Help
        exit;;
      c) 
        Create
        exit;;
      s) 
        Start
        exit;;
      d)
        Delete
        exit;;
     \?) # Invalid option
        echo "Error: Invalid option"
        exit;;
   esac
done

Help

