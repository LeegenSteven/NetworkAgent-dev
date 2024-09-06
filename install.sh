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

export GOOGLE_PROJECT_NUMBER=`gcloud projects describe $GOOGLE_PROJECT --format="value(projectNumber)"`
export GOOGLE_ACTIVE_USER=`gcloud auth list --filter=status:ACTIVE --format="value(account)"`
export GOOGLE_REPO="networkagent"

############################################################
# Create keys and manifest files                           #
############################################################
Create()
{
    echo "########################################"
    echo "Setting project to $GOOGLE_PROJECT"
    echo "########################################"
    gcloud config set project $GOOGLE_PROJECT

    # Make sure the active GCP user has proper permissions
    echo "########################################"
    echo "Grant GCP permissions to GCP active user: $GOOGLE_ACTIVE_USER"
    echo "########################################"
    gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="user:$GOOGLE_ACTIVE_USER" --role="roles/logging.logWriter"

    # enable GCP Services API needed
    echo "########################################"
    echo "Enabling required GCP services API for project $GOOGLE_PROJECT"
    echo "########################################"
    gcloud services enable --project=$GOOGLE_PROJECT artifactregistry.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT cloudbuild.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT compute.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT container.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT run.googleapis.com

    # Create artifact repository
    echo "########################################"
    echo "Create Artifact Repository "
    echo "########################################"
    gcloud artifacts repositories create $GOOGLE_REPO --repository-format=docker --location=$GOOGLE_REGION --description="Network Agent Repository" --quiet

    # Configure Cloud Build service account
    echo "########################################"
    echo "Setup Cloud Build service account permissions "
    echo "########################################"
    CLOUD_BUILD_COMPUTE_SVC_ACCOUNT="${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
    gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$CLOUD_BUILD_COMPUTE_SVC_ACCOUNT" --role="roles/storage.objectUser"
    gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$CLOUD_BUILD_COMPUTE_SVC_ACCOUNT" --role="roles/logging.logWriter"
    gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$CLOUD_BUILD_COMPUTE_SVC_ACCOUNT" --role="roles/artifactregistry.writer"

    # Test if google compute ssh keys exist, it not generate them
    if ! test -f google-compute; then
        echo "#############################################################"
        echo "SSH key google-compute does not exist, generating new keys..."
        echo "#############################################################"
        ssh-keygen -o -a 100 -t ed25519 -f google-compute -C networkagent -P ""
    fi

    echo "###################################################"
    echo "Found google-compute ssh keys, copying where needed"
    echo "###################################################"
    cp google-compute operator/src
    cp google-compute.pub operator/src

    echo "Add ssh key to OS login"
    gcloud compute os-login ssh-keys add --key-file=google-compute.pub --project=$GOOGLE_PROJECT --ttl=1d

    echo "Templating k8s manifest files"

    # grab the public ssh key for templating into VM manifests
    export GOOGLE_SSH_KEY=$(cat google-compute.pub)

    # check if SERVICE ACCOUNT exists
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter=name:"networkagent@"`

    # Create the service account if it doesnt exist
    if [ -z "${GOOGLE_SERVICE_ACCOUNT}" ]; then
        echo "########################################"
        echo "No Service Account, trying to create one"
        echo "########################################"
        gcloud iam service-accounts create networkagent --description="Network Agent Service Account" --display-name="Network Agent"
        # recreate the service account environment variable
        export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter=name:"networkagent@"`
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/editor"
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
        echo "#########################"
        echo "copying networkagent.json"
        echo "#########################"
        cp networkagent.json tools/src
        cp networkagent.json operator/src
        cp networkagent.json networkagent/src
    else
        echo "###########################################################################################################################################"
        echo "networkagent.json is empty, check your project is allowed to create service account keys or if you have exceeded the number of keys allowed."
        echo "###########################################################################################################################################"
        exit 0
    fi

    echo "####################################################"
    echo "generating environment yaml files"
    echo "####################################################"
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE environment/bigquery.j2 >  environment/bigquery.yaml
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE environment/configconnector.j2 > environment/configconnector.yaml
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE environment/networks.j2 > environment/networks.yaml

    echo "#######################################################"
    echo "generating networkagent, tools and operator yaml files"
    echo "#######################################################"
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO operator/deployment.j2 > operator/deployment.yaml
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO operator/cloudbuild.j2 > operator/cloudbuild.yaml
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO tools/deployment.j2 > tools/deployment.yaml
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO tools/cloudbuild.j2 > tools/cloudbuild.yaml
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO networkagent/deployment.j2 > networkagent/deployment.yaml
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO networkagent/cloudbuild.j2 > networkagent/cloudbuild.yaml

    echo "##############################"
    echo "generating customer site files"
    echo "##############################"
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
    echo "###########################"
    echo "Starting the network agent"
    echo "###########################"

    # check if SERVICE ACCOUNT exists
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter=name:"networkagent@"`

    # Create the service account if it doesnt exist
    if [ -z "${GOOGLE_SERVICE_ACCOUNT}" ]; then
        echo "Cannot find the service account - run this script with the -c option"
        exit 0
    fi

    echo "#####################"
    echo "Creating mgmt network"
    echo "#####################"
    gcloud compute networks create mgmt --subnet-mode=custom
    gcloud compute networks subnets create mgmt-subnet --network=mgmt --range=10.0.100.0/24 --region=$GOOGLE_REGION
    gcloud compute firewall-rules create mgmt-ingress --network=mgmt --allow=tcp,udp,icmp --source-ranges="0.0.0.0/0"
    gcloud compute routers create mgmt --network mgmt --region=$GOOGLE_REGION
    gcloud compute routers nats create mgmt --router=mgmt --region=$GOOGLE_REGION --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges --enable-logging

    # create the GKE cluster
    echo "###################################################"
    echo "Creating GKE cluster - this will take a few minutes"
    echo "###################################################"
    gcloud container clusters create networkautomation \
    --release-channel stable \
    --addons ConfigConnector \
    --enable-ip-alias \
    --service-account $GOOGLE_SERVICE_ACCOUNT\
    --scopes default,storage-full,cloud-platform,bigquery \
    --workload-pool $GOOGLE_PROJECT.svc.id.goog \
    --zone $GOOGLE_ZONE\
    --node-locations $GOOGLE_ZONE \
    --num-nodes 5 \
    --network mgmt \
    --subnetwork mgmt-subnet

    # On glinux machines gcloud components cannot be installed
    # through gcloud. apt must be used instead
    if [[ `uname -v` =~ "rodete" ]]; then
        sudo apt install kubectl
        sudo apt-get install google-cloud-cli-gke-gcloud-auth-plugin
    else
        gcloud components install kubectl
        gcloud components install gke-gcloud-auth-plugin # for GKE 1.26+
    fi

    gcloud container clusters get-credentials networkautomation --region=$GOOGLE_ZONE

    gcloud iam service-accounts add-iam-policy-binding \
    $GOOGLE_SERVICE_ACCOUNT \
        --member="serviceAccount:$GOOGLE_PROJECT.svc.id.goog[cnrm-system/cnrm-controller-manager]" \
        --role="roles/iam.workloadIdentityUser"

    kubectl create namespace automation
    kubectl annotate namespace automation cnrm.cloud.google.com/project-id=$GOOGLE_PROJECT
    kubectl config set-context --current --namespace automation
    kubectl apply -f environment/configconnector.yaml

    echo "################################################"
    echo "Waiting for cnrm-controller-manager-0 to start "
    echo "################################################"

    # kubectl wait -n cnrm-system --for=condition=Ready pod cnrm-controller-manager-0
    while [[ $(kubectl get pods -n cnrm-system cnrm-controller-manager-0 -o 'jsonpath={..status.conditions[?(@.type=="Ready")].status}' 2>/dev/null) != "True" ]]; do
        sleep 20
        echo "sleeping for 20 secs..."
    done

    echo "##################################"
    echo "Deploy the Operator"
    echo "##################################"
    Operator

    # start the network, prometheus monitor and the customer locations
    kubectl apply -f environment/networks.yaml
    kubectl apply -f environment/bigquery.yaml
    kubectl apply -f environment/prometheus.yaml
    kubectl apply -f sample-services/customersites
}

############################################################
# Delete GKE, config connector and customer sites          #
############################################################
Delete()
{
    read -p "Are you sure you want to delete the environment configuration (y/n)? " choice
    case "$choice" in 
        y|Y ) echo "proceeding to delete environment configuration";;
        n|N ) exit 0;;
        * ) echo "please enter y/n";;
    esac

    echo "#######################################"
    echo "Deleting environment manifests and keys"
    echo "#######################################"
    rm operator/deployment.yaml
    rm operator/src/google-compute*
    rm operator/src/networkagent.json

    rm tools/deployment.yaml
    rm tools/src/networkagent.json

    rm networkagent/deployment.yaml
    rm networkagent/src/networkagent.json

    rm environment/bigquery.yaml
    rm environment/configconnector.yaml
    rm environment/networks.yaml

    rm networkagent.json
    rm google-compute*

    echo "Delete any deployed connectivity services"

    echo "Delete customer location"

    rm sample-services/customersites/*yaml

    echo "Delete network automation GKE"
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
# Kill the environment resources                           #
############################################################
Kill()
{
    read -p "Are you sure you want to kill the environment(y/n)? " choice
    case "$choice" in 
        y|Y ) echo "proceeding to kill the environment";;
        n|N ) exit 0;;
        * ) echo "please enter y/n";;
    esac

    echo "##############################################"
    echo "Killing the environment - will take a few mins"
    echo "##############################################"

    kubectl delete -f sample-services/site2site/service.yaml
    kubectl delete -f sample-services/customersites
    kubectl delete -f environment/prometheus.yaml
    kubectl delete -f environment/bigquery.yaml
    kubectl delete -f environment/networks.yaml
    gcloud run services delete network-agent-api --region=$GOOGLE_REGION --quiet

    echo "#####################"
    echo "Deleting GKE Cluster"
    echo "#####################"
    gcloud container clusters delete networkautomation --region=$GOOGLE_ZONE --quiet

    echo "#####################"
    echo "Deleting mgmt network"
    echo "#####################"
    gcloud compute routers delete mgmt --region=$GOOGLE_REGION --quiet
    gcloud compute firewall-rules delete mgmt-ingress --region=$GOOGLE_REGION --quiet
    gcloud compute networks subnets delete mgmt-subnet --region=$GOOGLE_REGION --quiet
    gcloud compute networks delete mgmt --region=$GOOGLE_REGION --quiet

}

############################################################
# Build and deploy the operator                            #
############################################################
Operator()
{
    if ! test -f operator/deployment.yaml; then
        echo "No deployment.yaml found - you can generate by running ./install.sh -c"
        exit 0
    fi

    cd operator
    gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml
    kubectl apply -f config
    kubectl delete -f deployment.yaml
    kubectl apply -f deployment.yaml
    kubectl get pods 
    cd ..
}

############################################################
# Build and deploy the tools                               #
############################################################
Tools()
{
    if ! test -f tools/deployment.yaml; then
        echo "No deployment.yaml found - you can generate by running ./install.sh -c"
        exit 0
    fi

    cd tools
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter=name:"networkagent@"`
    gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml
    gcloud run deploy network-agent-api --image $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networktools:latest --region $GOOGLE_REGION --service-account $GOOGLE_SERVICE_ACCOUNT --update-env-vars GOOGLE_PROJECT=$GOOGLE_PROJECT,GOOGLE_REGION=$GOOGLE_REGION,GOOGLE_ZONE=$GOOGLE_ZONE
    cd ..
}

############################################################
# Build and deploy the networkagent                        #
############################################################
Networkagent()
{
    if ! test -f networkagent/deployment.yaml; then
        echo "No deployment.yaml found - you can generate by running ./install.sh -c"
        exit 0
    fi

    cd networkagent
    gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml
    kubectl delete -f deployment.yaml
    kubectl apply -f deployment.yaml
    kubectl get pods
    cd ..
}

############################################################
# Help                                                     #
############################################################
Help()
{
   # Display Help
   echo "Network Agent environment manager."
   echo
   echo "Syntax: install.sh [-c|-s|-o|-r|-n|-k|-d]"
   echo "options:"
   echo "  -c     create keys and manifests."
   echo "  -s     start network agent environment."
   echo "  -o     build and deploy the operator"
   echo "  -t     build and deploy the rest tools"
   echo "  -n     build and deploy the networkagent"
   echo "  -k     kill the environment resources."
   echo "  -d     delete the network agent environment."
   echo
}

############################################################
# Process the input options. Add options as needed.        #
############################################################
# Get the options
while getopts ":hcsotnkd" option; do
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
      o) 
        Operator
        exit;;
      t) 
        Tools
        exit;;
      n) 
        Networkagent
        exit;;
      k) 
        Kill
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

