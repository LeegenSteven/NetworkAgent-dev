#/usr/bin/bash

# Test if google compute exists
if ! test -f google-compute; then
  echo "SSH key google-compute does not exist."
  exit 0
fi

if [ -z "${GOOGLE_PROJECT}" ] || [ -z "${GOOGLE_REGION}" ] || [ -z "${GOOGLE_ZONE}" ] || [ -z "${GOOGLE_USER}" ]; then
    echo "You must set GOOGLE_PROJECT, GOOGLE_REGION, and GOOGLE_ZONE environment variables"
    exit 0
fi

echo "templating k8s manifest files"
export GOOGLE_SSH_KEY=$(cat ./google-compute.pub)

echo "generating monitoring and configconnector yaml files"
jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE monitoring.j2 >  monitoring.yaml
jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE configconnector.j2 > configconnector.yaml

echo "generating networwkagent, tools and operator yaml files"
jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../operator/deployment.j2 > ../operator/deployment.yaml
jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../tools/deployment.j2 > ../tools/deployment.yaml
jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../networkagent/deployment.j2 > ../tools/networkagent.yaml

echo "generating customer site files"
jinja -E GOOGLE_USER -E GOOGLE_SSH_KEY -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../sample-services/customersites/london.j2 > ../sample-services/customersites/london.yaml
jinja -E GOOGLE_USER -E GOOGLE_SSH_KEY -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../sample-services/customersites/sydney.j2 > ../sample-services/customersites/sydney.yaml
jinja -E GOOGLE_USER -E GOOGLE_SSH_KEY -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../sample-services/customersites/singapore.j2 > ../sample-services/customersites/singapore.yaml
jinja -E GOOGLE_USER -E GOOGLE_SSH_KEY -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE ../sample-services/customersites/newyork.j2 > ../sample-services/customersites/newyork.yaml
