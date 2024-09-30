# Gitea

Create a git repo for the __automation__ namespace

To start the gitea VM run the following. This will create a VM and install gitea

```
kubectl apply -f environment/git.yaml
```

To get access to the gitea UI run the following command.

```
gcloud compute start-iap-tunnel gitea 3000 --local-host-port=localhost:3000 --zone=$GOOGLE_ZONE
```

Then open a browser at http://127.0.0.1:3000

To get ssh access to the VM run the following command

```
gcloud compute ssh monitor --zone=$GOOGLE_ZONE --tunnel-through-iap --project=$GOOGLE_PROJECT
```


```
git init
git checkout -b main
git add *yaml
git commit -m "first commit"
git remote add origin http://brian:password123@127.0.0.1:3000/brian/acme-services.git
git push -u origin main
```