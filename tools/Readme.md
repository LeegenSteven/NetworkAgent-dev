# LLM Tools

This folder contains a set of tools that can be used by LLMs to interact with the Kubernetes network orchestration layer.


## Build

Authenticate with the repo

```
gcloud auth configure-docker LOCATION-docker.pkg.dev
```

Build with cloud run

```
gcloud builds submit --tag us-docker.pkg.dev/cloudrun/container/hello:latest
```

Build locally

```
docker build . -t us-docker.pkg.dev/cloudrun/container/hello:latest
docker push 
```

## Authentication

create service account, download credentials to tools.json

update environment variables ??

## Deploy

https://cloud.google.com/run/docs/deploying?skip_cache=true