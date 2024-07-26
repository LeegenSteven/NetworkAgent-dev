# Edge Appliance Virtual Machine Dummy Environment


## Create SSH keys

Add local key to project

```
ssh-keygen -o -a 100 -t ed25519 -f google-compute -C briannaughton
gcloud compute os-login ssh-keys add --key-file=google-compute.pub --project=free5gc-384814 --ttl=1d
```
