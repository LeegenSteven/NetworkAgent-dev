
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

############################################################
# Check pre-requisites                                     #
############################################################
# test if gcloud exists
if ! command -v gcloud &> /dev/null
then
    echo "gcloud could not be found, you must install it"
    exit 1
fi

# test if jinja exists
if ! command -v jinja &> /dev/null
then
    echo "jinja could not be found, you must run 'pip install jinja-cli'"
    exit 1
fi

# The WEBAPPS_PWD and WEBAPPS_LOGIN used for all web front ends like Gitea, Streamlit NW Agent
# This is to avoid hard coding the passwd in source code
if [ -z "${GOOGLE_PROJECT}" ] || [ -z "${GOOGLE_REGION}" ] || \
   [ -z "${GOOGLE_ZONE}" ] || [ -z "${GOOGLE_USER}" ] || \
   [ -z "${WEBAPPS_PWD}" ] || [ -z "${WEBAPPS_LOGIN}" ]; then
    echo "You must set GOOGLE_USER, GOOGLE_PROJECT, GOOGLE_REGION, GOOGLE_ZONE, WEBAPPS_LOGIN and WEBAPPS_PWD environment variables"
    exit 1
fi

# Make sure that the designated project has a billing account. If not all else will fail
gcloud beta billing projects describe $GOOGLE_PROJECT > /dev/null 2>&1
if [[ $? -ne 0 ]]; then
    echo "Project $GOOGLE_PROJECT has no billing account. Billing must be enabled prior to activation of GCP services"
    exit 1
fi

# Check that we can use non shielded VMs
shielded_vm_enforced=$(gcloud resource-manager org-policies describe compute.requireShieldedVm --project $GOOGLE_PROJECT --effective --format="value(booleanPolicy.enforced)")
if [ "$shielded_vm_enforced" = "True" ]; then
    echo "compute.requireShieldedVm is enforced on this project. Please change this org Policy to False before proceeding"
    exit 1
fi

# Check that we can use external IP addresses (needed by the gitea VM)
external_ip_access=$(gcloud resource-manager org-policies describe compute.vmExternalIpAccess --project $GOOGLE_PROJECT --effective --format="value(listPolicy.allValues)")
if [ "$external_ip_access" = "DENY" ]; then
    echo "compute.vmExternalIpAccess is denied on this project. Please change this org Policy to ALLOW before proceeding"
    exit 1
fi

# Check that VM can IP forward (needed by the gitea VM)
vm_can_ip_forward=$(gcloud resource-manager org-policies describe compute.vmCanIpForward --project $GOOGLE_PROJECT --effective --format="value(listPolicy.allValues)")
if [ "$vm_can_ip_forward" = "DENY" ]; then
    echo "compute.vmCanIpForward is denied on this project. Please change this org Policy to ALLOW before proceeding"
    exit 1
fi

# Check that account can be created on service accounts
svc_account_key_disabled=$(gcloud resource-manager org-policies describe iam.disableServiceAccountKeyCreation --project $GOOGLE_PROJECT --effective --format="value(booleanPolicy.enforced)")
if [ "$svc_account_key_disabled" = "True" ]; then
    echo "iam.disableServiceAccountKeyCreation is enforced on this project. Please change this org Policy to False before proceeding"
    exit 1
fi

export GOOGLE_PROJECT_NUMBER=`gcloud projects describe $GOOGLE_PROJECT --format="value(projectNumber)"`
export GOOGLE_ACTIVE_USER=`gcloud auth list --filter=status:ACTIVE --format="value(account)"`
export GOOGLE_REPO="networkagent"
export GOOGLE_NAMESPACE="automation"
export GOOGLE_SPANNER_INSTANCE="networktopology-instance"
export GOOGLE_SPANNER_DATABASE="networktopology-db"

SINK_NAME="nwoplogs-sink"
TOPIC_NAME="nwoplogs-topic"
CAPTURE_LOG_FUNCTION="capture_log"

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
    for role in "roles/logging.logWriter" "roles/spanner.databaseReader"; do
        echo "$role"
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="user:$GOOGLE_ACTIVE_USER" --role="$role" --no-user-output-enabled
        # roles/spanner.databaseReader needed for the COlab Notebook to access the graph database
    done

    # enable GCP Services API needed
    echo "########################################"
    echo "Enabling required GCP services API for project $GOOGLE_PROJECT"
    echo "########################################"
    gcloud services enable --project=$GOOGLE_PROJECT artifactregistry.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT cloudbuild.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT cloudfunctions.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT eventarc.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT compute.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT container.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT gkehub.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT anthos.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT run.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT bigquery.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT spanner.googleapis.com
    # For vertex AI workbench
    gcloud services enable --project=$GOOGLE_PROJECT notebooks.googleapis.com
    # for colab enterprise in addition to compute engine api
    gcloud services enable --project=$GOOGLE_PROJECT aiplatform.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT dataform.googleapis.com

    # Configure Cloud Build service account
    echo "########################################"
    echo "Setup Cloud Build service account permissions "
    echo "########################################"
    CLOUD_BUILD_COMPUTE_SVC_ACCOUNT="${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
    echo "Granting roles to Cloud Build service account ${CLOUD_BUILD_COMPUTE_SVC_ACCOUNT}..."
    for role in "roles/storage.objectUser" "roles/logging.logWriter" "roles/artifactregistry.writer" "roles/cloudbuild.builds.builder"; do
        echo "$role"
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$CLOUD_BUILD_COMPUTE_SVC_ACCOUNT" \
          --role="$role" --no-user-output-enabled
    done

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
    gcloud compute os-login ssh-keys add --key-file=google-compute.pub --project=$GOOGLE_PROJECT --ttl=100d

    echo "Templating k8s manifest files"

    # grab the public ssh key for templating into VM manifests
    export GOOGLE_SSH_KEY=$(cat google-compute.pub)

    # check if SERVICE ACCOUNT exists
    export GOOGLE_SERVICE_ACCOUNT="networkagent@${GOOGLE_PROJECT}.iam.gserviceaccount.com"
    gcloud iam service-accounts describe $GOOGLE_SERVICE_ACCOUNT > /dev/null 2>&1

    # Create the service account if it doesn't exist
    if [[ $? -ne 0 ]]; then
        echo "########################################"
        echo "No Service Account, trying to create one"
        echo "########################################"
        gcloud iam service-accounts create networkagent --description="Network Agent Service Account" --display-name="Network Agent"
        if [[ $? -ne 0 ]]; then
            echo "Creation of the GKE cluster service account failed. Fix the error and re-run the install command"
            exit 1
        fi

        echo "Granting permissions to the GKE Cluster service account..."
        for role in "roles/editor" "roles/container.admin" "roles/compute.admin" \
            "roles/compute.networkAdmin" "roles/iam.serviceAccountAdmin" "roles/monitoring.metricWriter" \
            "roles/aiplatform.user"; do
            echo "$role"   
            gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" \
              --role="$role" --no-user-output-enabled
        done

    fi

    # if the creadentail file doesn't exist or as a zero byte size 
    # then create it
    if [[ ! ( -f "networkagent" && -s "networkagent" ) ]]; then
        echo "Creating the application credential file for service account $GOOGLE_SERVICE_ACCOUNT..."
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
        echo "#############################################################"
        echo "networkagent.json is empty, check your project is allowed to "
        echo "create service account keys or if you have exceeded the number "
        echo "of keys allowed."
        echo "##############################################################"
        exit 1
    fi

    echo "####################################################"
    echo "generating environment yaml files"
    echo "####################################################"
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE environment/bigquery.j2 >  environment/bigquery.yaml
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_PROJECT_NUMBER -E GOOGLE_SERVICE_ACCOUNT environment/logsink.j2 >  environment/logsink.yaml
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_SPANNER_DATABASE -E GOOGLE_SPANNER_INSTANCE environment/spanner.j2 >  environment/spanner.yaml
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE environment/configconnector.j2 > environment/configconnector.yaml
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE environment/networks.j2 > environment/networks.yaml

    echo "#######################################################"
    echo "generating networkagent, tools and operator yaml files"
    echo "#######################################################"
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO -E WEBAPPS_LOGIN -E WEBAPPS_PWD operator/deployment.j2 > operator/deployment.yaml
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO operator/cloudbuild.j2 > operator/cloudbuild.yaml
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO tools/deployment.j2 > tools/deployment.yaml
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO tools/cloudbuild.j2 > tools/cloudbuild.yaml
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO -E WEBAPPS_LOGIN -E WEBAPPS_PWD networkagent/deployment.j2 > networkagent/deployment.yaml
    jinja -E GOOGLE_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO networkagent/cloudbuild.j2 > networkagent/cloudbuild.yaml

}

############################################################
# Start GKE, config connector and customer sites           #
############################################################
Start()
{
   # Create artifact repository
    echo "########################################"
    echo "Create Artifact Repository "
    echo "########################################"
    gcloud artifacts repositories describe $GOOGLE_REPO --location=$GOOGLE_REGION > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        gcloud artifacts repositories create $GOOGLE_REPO --repository-format=docker --location=$GOOGLE_REGION --description="Network Agent Repository" --quiet
    fi
    gcloud auth configure-docker $GOOGLE_REGION-docker.pkg.dev --quiet

    echo "###########################"
    echo "Starting the network agent"
    echo "###########################"
    # check if SERVICE ACCOUNT exists
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter="networkagent@${GOOGLE_PROJECT}."`
    echo "GKE Cluster Service Account: $GOOGLE_SERVICE_ACCOUNT"

    # Create the service account if it doesnt exist
    if [ -z "${GOOGLE_SERVICE_ACCOUNT}" ]; then
        echo "Cannot find the service account - run this script with the -c option first"
        exit 1
    fi

    echo "#####################"
    echo "Creating mgmt network"
    echo "#####################"
    (gcloud compute networks describe mgmt > /dev/null 2>&1) || \
        gcloud compute networks create mgmt --subnet-mode=custom
    (gcloud compute networks subnets describe mgmt-subnet --region=$GOOGLE_REGION > /dev/null 2>&1) || \
        gcloud compute networks subnets create mgmt-subnet --network=mgmt --range=10.0.100.0/24 --region=$GOOGLE_REGION
    (gcloud compute firewall-rules describe mgmt-ingress > /dev/null 2>&1) || \
        gcloud compute firewall-rules create mgmt-ingress --network=mgmt --allow=tcp,udp,icmp --source-ranges="0.0.0.0/0"
    (gcloud compute routers describe mgmt --region=$GOOGLE_REGION > /dev/null 2>&1) || \
        gcloud compute routers create mgmt --network mgmt --region=$GOOGLE_REGION
    (gcloud compute routers nats describe mgmt --router=mgmt --region=$GOOGLE_REGION > /dev/null 2>&1) || \
        gcloud compute routers nats create mgmt --router=mgmt --region=$GOOGLE_REGION --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges --enable-logging

    # create the GKE cluster
    echo "###################################################"
    echo "Creating GKE cluster - this will take a few minutes"
    echo "###################################################"
    (gcloud container clusters describe networkautomation --zone=$GOOGLE_ZONE > /dev/null 2>&1) || \
    gcloud container clusters create networkautomation \
    --release-channel stable \
    --addons ConfigConnector \
    --enable-ip-alias \
    --service-account $GOOGLE_SERVICE_ACCOUNT\
    --scopes "default,storage-full,cloud-platform,bigquery" \
    --workload-pool $GOOGLE_PROJECT.svc.id.goog \
    --zone $GOOGLE_ZONE\
    --node-locations $GOOGLE_ZONE \
    --num-nodes 2 \
    --machine-type "n1-standard-4" \
    --enable-fleet \
    --network mgmt \
    --subnetwork mgmt-subnet 

    # On glinux machines gcloud components cannot be installed
    # through gcloud. apt must be used instead
    if [[ `uname -v` =~ "rodete" ]]; then
        sudo apt install kubectl
        sudo apt-get install google-cloud-cli-gke-gcloud-auth-plugin
    else
        gcloud components install kubectl
        gcloud components install kpt
        gcloud components install gke-gcloud-auth-plugin # for GKE 1.26+
    fi

    gcloud container clusters get-credentials networkautomation --region=$GOOGLE_ZONE

    gcloud iam service-accounts add-iam-policy-binding \
    $GOOGLE_SERVICE_ACCOUNT \
        --member="serviceAccount:$GOOGLE_PROJECT.svc.id.goog[cnrm-system/cnrm-controller-manager]" \
        --role="roles/iam.workloadIdentityUser"

    # Setup the GKE namespace we'll be using
    kubectl create namespace $GOOGLE_NAMESPACE
    kubectl annotate namespace $GOOGLE_NAMESPACE cnrm.cloud.google.com/project-id=$GOOGLE_PROJECT
    kubectl config set-context --current --namespace $GOOGLE_NAMESPACE

    # create and attach operator service account to networkagent service account for workload identity
    kubectl create serviceaccount networkoperator-account --namespace $GOOGLE_NAMESPACE
    gcloud iam service-accounts add-iam-policy-binding $GOOGLE_SERVICE_ACCOUNT \
        --role roles/iam.workloadIdentityUser \
        --member "serviceAccount:$GOOGLE_PROJECT.svc.id.goog[$GOOGLE_NAMESPACE/networkoperator-account]"

    # Grant access permissions to the GKE cluster
    # See https://cloud.google.com/spanner/docs/connect-gke-cluster
    # For an unknown reason granting to the service account (line below) doesn't work...
    # gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
    #  --member="principal://iam.googleapis.com/projects/${GOOGLE_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${GOOGLE_PROJECT}.svc.id.goog/subject/ns/${GOOGLE_NAMESPACE}/sa/${GOOGLE_SERVICE_ACCOUNT}" \
    #  --role=roles/spanner.databaseUser --condition=None
    #
    # So here is a variant that grants the spanner permission to all service accounts
    # in the designated namespace. This one works.
    #
    # Same to give the operator access to the Vertex AI prediction API
    for role in "roles/spanner.databaseUser" "roles/aiplatform.user"; do
        gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
            --member="principalSet://iam.googleapis.com/projects/${GOOGLE_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${GOOGLE_PROJECT}.svc.id.goog/namespace/${GOOGLE_NAMESPACE}" \
            --role="$role" --condition=None --no-user-output-enabled
    done   
    echo "done."

    # Setup the one config connector we will be using 
    kubectl apply -f environment/configconnector.yaml

    echo "################################################"
    echo "Waiting for cnrm-controller-manager-0 to start... "
    echo "################################################"

    # kubectl wait -n cnrm-system --for=condition=Ready pod cnrm-controller-manager-0
    while [[ $(kubectl get pods -n cnrm-system cnrm-controller-manager-0 -o 'jsonpath={..status.conditions[?(@.type=="Ready")].status}' 2>/dev/null) != "True" ]]; do
        sleep 20
        echo "sleeping for 20 secs..."
    done
    echo "Ready !"

    # Start ConfigSync operator in cluster
    gcloud beta container fleet config-management enable --project=$GOOGLE_PROJECT
    gcloud beta container fleet config-management apply --membership=networkautomation --config=./environment/configsync.yaml --project=$GOOGLE_PROJECT

    # Setup Spanner and wait until it's ready as we need it to be up and
    # running before the Operator is deployed so as not to miss any
    # creation events in the operator (especially on the networking part)
    # 
    echo "####################################"
    echo "Waiting for Spanner DB to come up..."
    echo "####################################"
    
    echo "Creating Spanner database ${GOOGLE_SPANNER_INSTANCE}..."
    kubectl apply -f environment/spanner.yaml -l "kind=spanner-instance"
    while [[ $(kubectl get spannerinstance $GOOGLE_SPANNER_INSTANCE -o 'jsonpath={..status.conditions[?(@.type=="Ready")].status}' 2>/dev/null) != "True" ]]; do
        sleep 20
        echo "sleeping for 20 secs..."
    done
    echo "Spanner instance ready !"

    # Work around because the edition spec is not supported in the manifest file
    # Same for backup schedule updated to None as backup creation make the DB deletion
    # more complex (not needed in this PoC)
    # (See https://b.corp.google.com/issues/372631209)
    echo "Updating Spanner instance to Enterprise Edition"
    gcloud spanner instances update $GOOGLE_SPANNER_INSTANCE --edition=ENTERPRISE
    echo "Updating Spanner instance to no backup schedule"
    gcloud spanner instances update $GOOGLE_SPANNER_INSTANCE --default-backup-schedule-type=NONE

    echo "Creating Spanner database ${GOOGLE_SPANNER_DATABASE}..."
    kubectl apply -f environment/spanner.yaml -l "kind=spanner-database"
    while [[ $(kubectl get spannerdatabase $GOOGLE_SPANNER_DATABASE -o 'jsonpath={..status.conditions[?(@.type=="Ready")].status}' 2>/dev/null) != "True" ]]; do
        sleep 20
        echo "sleeping for 20 secs..."
    done
    echo "Spanner database ready !"

    echo "#####################################"
    echo "Deploy the Operator, networks and git"
    echo "#####################################"
    Operator

    echo "#####################################"
    echo "Create Operator Log Sink and capture"
    echo "#####################################"
    Log

    # start the network and git repos
    kubectl apply -f environment/networks.yaml
    # kubectl apply -f environment/bigquery.yaml

    # I tried hard to create the Log Sink to BQ or PubSub with Config Connector
    # to no avail. I couldn't fix the dataset or topic access permission problem :-(
    # That's why it is created by hand above
    # kubectl apply -f environment/logsink.yaml
    # kubectl apply -f environment/prometheus.yaml
    kubectl apply -f environment/git.yaml
    # kubectl apply -f environment/free5gc-build.yaml

    # echo "##################################"
    # echo "Deploy Porch                      "
    # echo "##################################"
    # Porch

    # Say how to access the gitea server
    while [[ $(kubectl get gitea gitea -o 'jsonpath={..status.create_gitea.status}' 2>/dev/null) != "Running" ]]; do
        sleep 60
        echo "waiting for Gitea to be ready, sleeping for 60 secs..."
    done
    gitea_host=$(kubectl get gitea gitea -o 'jsonpath={..status.create_gitea.external_ip_address}')
    echo -e "\nGitea server is available at:\n\thttps://$gitea_host:3000/explore/repos\n"
    echo "You can clone the git repos as follows (username/password = ${WEBAPPS_LOGIN}/${WEBAPPS_PWD})"
    echo "  git clone https://$gitea_host:3000/networkagent/core -c http.sslVerify=false"
    echo "  git clone https://$gitea_host:3000/networkagent/dublin -c http.sslVerify=false"
    echo "  git clone https://$gitea_host:3000/networkagent/london -c http.sslVerify=false"
    echo "  git clone https://$gitea_host:3000/networkagent/london-cluster -c http.sslVerify=false"
    echo "  git clone https://$gitea_host:3000/networkagent/newyork -c http.sslVerify=false"
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

    echo "######################"
    echo "Deleting Artifact Repo"
    echo "######################"
    gcloud artifacts repositories delete $GOOGLE_REPO --location=$GOOGLE_REGION --quiet

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
    rm environment/spanner.yaml
    rm environment/configconnector.yaml
    rm environment/networks.yaml

    rm networkagent.json
    rm google-compute*

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
    kubectl config set-context --current --namespace $GOOGLE_NAMESPACE
    
    # kubectl delete -f sample-services/connectivity-services
    # kubectl delete -f sample-services/locations
    # kubectl delete -f environment/prometheus.yaml
    kubectl delete -f environment/git.yaml
    kubectl delete -f environment/free5gc-build.yaml
    # kubectl delete -f environment/bigquery.yaml
    kubectl delete -f environment/spanner.yaml
    # Sometimes kopf finalizers are not removed from the network resources
    # and the kubectl command below hangs for ever. So clear the finalizers after
    # a certain timeout if it is still hanging 

    # Delete log sink, pub/sub topic and log processing Cloud Function
    gcloud logging sinks delete $SINK_NAME --quiet
    gcloud pubsub topics delete $TOPIC_NAME --quiet
    gcloud functions delete $CAPTURE_LOG_FUNCTION --region=$GOOGLE_REGION  --quiet
    #bq rm --recursive --force --dataset nwoplogs

    # Launch the kubectl command in the backgroun
    kubectl delete -f environment/networks.yaml &
    job_id=$!

    # For the timeout duration check that the command is still running
    # if the timeout popped (return code 124) then clean up the finalizers
    # to unblock the kubectl command
    timeout 2m sh -c "while kill -0 $job_id 2>/dev/null; do sleep 1; done"
    if [ $? -eq 124 ]; then
      kubectl patch computefirewalls dataplane --patch '{"metadata":{"finalizers":[]}}'  --type=merge
      kubectl patch computesubnetworks dataplane --patch '{"metadata":{"finalizers":[]}}'  --type=merge
      kubectl patch computenetworks dataplane --patch '{"metadata":{"finalizers":[]}}'  --type=merge
    fi

    gcloud run services delete network-agent-api --region=$GOOGLE_REGION --quiet
    gcloud run services delete network-agent --region=$GOOGLE_REGION --quiet

    echo "#####################"
    echo "Deleting GKE Cluster"
    echo "#####################"
    gcloud container clusters delete networkautomation --region=$GOOGLE_ZONE --quiet

    echo "#####################"
    echo "Deleting mgmt network"
    echo "#####################"
    gcloud compute routers delete mgmt --region=$GOOGLE_REGION --quiet
    gcloud compute firewall-rules delete mgmt-ingress --project=$GOOGLE_PROJECT --quiet
    gcloud compute networks subnets delete mgmt-subnet --region=$GOOGLE_REGION --quiet
    gcloud compute networks delete mgmt --project=$GOOGLE_PROJECT --quiet

}

############################################################
# Build and deploy the operator                            #
############################################################
Operator()
{
    if ! test -f operator/deployment.yaml; then
        echo "No deployment.yaml found - you can generate by running ./install.sh -c"
        exit 1
    fi

    cd operator
    gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml
    kubectl apply -f config
    kubectl delete -f deployment.yaml
    kubectl apply -f deployment.yaml
    kubectl get pods 
    echo "Waiting for deployment to be ready..."
    kubectl rollout status deployment gitea-operator -n $GOOGLE_NAMESPACE --timeout=120s
    kubectl rollout status deployment free5gc-operator -n $GOOGLE_NAMESPACE --timeout=120s
    cd ..
}

############################################################
# Build and deploy the log capture                         #
############################################################
Log()
{
    # Create a  network log sink to bigquery and collect
    # logs from the network operator
    #
    # ==> Sink to BQ dataset
    #bq mk --location=$GOOGLE_REGION --description="Network operator logs" --dataset nwoplogs
    #gcloud logging sinks create nwoplogs-sink bigquery.googleapis.com/projects/${GOOGLE_PROJECT}/datasets/nwoplogs \
    #  --log-filter='resource.labels.project_id="networkagent-434609" AND resource.type="k8s_container" \
    #      AND resource.labels.cluster_name="networkautomation" AND resource.labels.namespace_name="automation"  \
    #      AND labels.python_logger!="kopf._cogs.clients.watching"' \
    #  --description="Network operator logs"
    #gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
    #    --member="serviceAccount:${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    #    --role="roles/bigquery.dataEditor" --condition=None --no-user-output-enabled
    #

    # ==> Sink to PubSub topic
    # Create the pubsub topic if it doesn't exist yet
    gcloud pubsub topics describe $TOPIC_NAME > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        echo "Creating Pub/Sub topic '${TOPIC_NAME}'..."
        gcloud pubsub topics create $TOPIC_NAME --project=${GOOGLE_PROJECT}
    else
        echo "Pub/Sub topic '${TOPIC_NAME}' already exists..."
    fi

    # Create the logging sink if it doesn't exist yet
    gcloud logging sinks describe $SINK_NAME > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        echo "Creating Logging sink '${SINK_NAME}'..."
        gcloud logging sinks create $SINK_NAME pubsub.googleapis.com/projects/${GOOGLE_PROJECT}/topics/${TOPIC_NAME} \
            --log-filter="resource.labels.project_id=\"${GOOGLE_PROJECT}\" 
                    AND resource.labels.container_name=\"free5gc-operator\"  
                    AND labels.python_logger!=\"kopf._cogs.clients.watching\"" \
            --description="Network operator logs"
    else
        echo "Logging sink '${SINK_NAME}' already exists..."
    fi

    # Grant the Cloud Logging service account used by the Log sink the right to publish 
    # log entries to the PubSub topic
    gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
        --member="serviceAccount:service-${GOOGLE_PROJECT_NUMBER}@gcp-sa-logging.iam.gserviceaccount.com" \
        --role="roles/pubsub.publisher" --condition=None --no-user-output-enabled

    # Give the eventarc service account (by default the compute service account of the
    # project) the permission to invoke the cloud run function
    gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
        --member="serviceAccount:${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/run.invoker" --condition=None --no-user-output-enabled
    # Give the Cloud Function service account (by default the compute service account of the
    # project) the permission to use (read/write) Spanner
    gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
        --member="serviceAccount:${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/spanner.databaseUser" --condition=None --no-user-output-enabled   
    # Give the Cloud Function service account (by default the compute service account of the
    # project) the permission to use Vertex AI (e.g. embedding generation)
    gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
        --member="serviceAccount:${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/aiplatform.user" --condition=None --no-user-output-enabled   

    # Create the Cloud Run function that receives the eventarc
    # events from pub/pub  and feed the Spanner DB
    echo "Deploying Log capture function..."
    gcloud functions deploy $CAPTURE_LOG_FUNCTION --source ./logcollector --runtime python312 \
      --trigger-topic $TOPIC_NAME  --entry-point=capture_log --memory=512MB \
      --project=$GOOGLE_PROJECT --region=$GOOGLE_REGION
}


############################################################
# Deploy the Porch systems                                 #
############################################################
Porch()
{
    if ! test -f deployment-blueprint.tar.gz; then
        wget https://github.com/kptdev/kpt/releases/download/porch%2Fv0.0.35/deployment-blueprint.tar.gz
        mkdir porch-install
        tar xzf ./deployment-blueprint.tar.gz -C porch-install
    fi
    kubectl apply -f porch-install
    kubectl wait deployment --for=condition=Available porch-server -n porch-system --timeout=300s
}


############################################################
# Build and deploy the tools                               #
############################################################
Tools()
{
    if ! test -f tools/deployment.yaml; then
        echo "No deployment.yaml found - you can generate by running ./install.sh -c"
        exit 1
    fi

    cd tools
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter=name:"networkagent@"`
    gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml
    gcloud run deploy network-agent-api --image $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/networktools:latest \
       --region $GOOGLE_REGION --service-account $GOOGLE_SERVICE_ACCOUNT \
       --update-env-vars GOOGLE_PROJECT=$GOOGLE_PROJECT,GOOGLE_REGION=$GOOGLE_REGION,GOOGLE_ZONE=$GOOGLE_ZONE
    cd ..
}

############################################################
# Build and deploy the networkagent                        #
############################################################
Networkagent()
{
    if ! test -f networkagent/deployment.yaml; then
        echo "No deployment.yaml found - you can generate by running ./install.sh -c"
        exit 1
    fi

    cd networkagent
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter="networkagent@${GOOGLE_PROJECT}."`
    gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml

    gcloud run deploy network-agent --image $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/networkagent:latest \
       --region $GOOGLE_REGION --service-account $GOOGLE_SERVICE_ACCOUNT \
       --update-env-vars GOOGLE_PROJECT=$GOOGLE_PROJECT \
       --update-env-vars GOOGLE_REGION=$GOOGLE_REGION \
       --update-env-vars GOOGLE_ZONE=$GOOGLE_ZONE \
       --update-env-vars WEBAPPS_PWD=${WEBAPPS_PWD} \
       --update-env-vars WEBAPPS_LOGIN=${WEBAPPS_LOGIN} \
       --update-env-vars NETWORK_AGENT_FILE="/agent/networkagent.json" \
       --allow-unauthenticated

    # Check if allUsers access is already granted. 
    # If not Allow allUsers to invoke the Cloud Run service
    gcloud run services get-iam-policy network-agent --region=$GOOGLE_REGION --project=$GOOGLE_PROJECT \
           --format="value(bindings.members)" 2>&1 | fgrep -q allUsers
    if [ $? -ne 0 ]; then
      gcloud run services add-iam-policy-binding network-agent --member='allUsers' --role='roles/run.invoker' \
            --region=$GOOGLE_REGION --project=$GOOGLE_PROJECT >/dev/null 2>&1
      if [ $? -eq 1 ]; then
        echo "ERROR : could not setup access for all Users on the Cloud Run service network-agent"
        echo "You must probably disable the Domain Restricted Sharing policy of your domain."
        echo "Then run this command again and re-enable the DRS policy"
        exit 1
      fi
    fi
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
   echo "Syntax: install.sh [-c|-s|-o|-l|-r|-n|-k|-d|-p]"
   echo "options:"
   echo "  -c     create network agent environment (keys, manifests,..)"
   echo "  -s     build and start network agent runtime (incl. the operator)"
   echo "  -o     build and deploy the network operator"
   echo "  -l     build and deploy the logs capture function"
   echo "  -t     build and deploy the rest tools"
   echo "  -n     build and deploy the networkagent"
   echo "  -k     stop and delete the network agent runtime (GKE cluster, VMS, DB, etc..)"
   echo "  -d     delete the network agent environment (keys, manifests...)."
   echo "  -p     deploy porch tools"
   echo 
   echo "Some typical use cases:"
   echo " - To create and run a network agent environment including the operator: ./install.sh -c; ./install.sh -s"
   echo " - To redeploy the operator alone : ./install.sh -o"
   echo " - To (re)deploy the network agent Web UI alone : ./install.sh -n"
   echo " - To regenerate the network agent runtime with the same environment setup: ./install.sh -k; ./install.sh -s"
   echo " - To recreate a complete environment and runtime from scratch: ./install.sh -k; ./install.sh -d; ./install.sh -c; ./install.sh -s"
}

############################################################
# Process the input options. Add options as needed.        #
############################################################
# Get the options
while getopts ":hcsoltnkdp" option; do
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
      l) 
        Log
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
      p)
        Porch
        exit;;
     \?) # Invalid option
        echo "Error: Invalid option"
        exit;;
   esac
done

Help

