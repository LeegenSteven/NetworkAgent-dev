# LLM Tools

This folder contains a set of tools that can be used by LLMs to interact with the Kubernetes network orchestration layer.


## Authentication

create service account, download credentials to tools.json

update environment variables ??


## Build and deploy

Authenticate with the docker repo

```
gcloud auth configure-docker europe-west2-docker.pkg.dev
```

To build and push run the following from the __NetworkAgent/tools__ directory

```
docker build . -t europe-west2-docker.pkg.dev/free5gc-384814/networkagent/networktools:latest
docker push europe-west2-docker.pkg.dev/free5gc-384814/networkagent/networktools:latest
```

https://cloud.google.com/run/docs/deploying?skip_cache=true


## Cloud Run

To run the service

```
gcloud run deploy --image europe-west2-docker.pkg.dev/free5gc-384814/networkagent/networktools:latest --service-account networktools@free5gc-384814.iam.gserviceaccount.com
```