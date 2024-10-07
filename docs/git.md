# Gitea

A gitea virtual machine is automatically created when the __install.sh -s__ runs.

However, if you want to reset the gitea environment you can delete and recreate the gitea virtual machine by running the following commands from the NetworkAgent directory.

```
kubectl delete -f environment/git.yaml
kubectl apply -f environment/git.yaml
```

## Log into Gitea

To get access to the gitea UI run the following command, you can find its public IP address by running the following command.

```
kubectl describe gitea
```

Then open a browser at https://<<external_ip_address>>:3000

Log into gitea with the username/password networkagent/password123.


## Clone the repos

Clone the infrastructure and network-services repositories in a local directory. 

```
git clone https://<<external_ip_address>>:3000/networkagent/infrastructure -c http.sslVerify=false
git clone https://<<external_ip_address>>:3000/networkagent/network-services -c http.sslVerify=false
```

Copy the sample customer infrastructure files from the NetworkAgent project as follows:

```
cd infrastructure
cp <NetworkAgent Dir>/sample-services/customer-infrastructure/*yaml .
git add .
git commit -m "Added customer infrastructure config"
git push
```

