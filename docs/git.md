# Gitea

A gitea virtual machine is automatically created when the __install.sh -s__ runs.

The k8s manifest can be found in __environment/git.yaml__

You must wait for the status to be __Ready__ by running the following

```
kubectl describe gitea
```

## Log into Gitea

To get access to the gitea UI run the following command to find the VM's public IP address.

```
kubectl get giteas gitea -o jsonpath='{.status.create_gitea.external_ip_address}'
```

Then open a browser at https://<<external_ip_address>>:3000

Log into gitea with the username/password __networkagent/your_password__ \
(where your password is the password that you setup at built time in the WEBAPPS_PWD environment variable)

## Clone the repos to your local machine

If you want to clone the infrastructure and network-services repositories to a local directory you can run the following commands. 

```
export GITEA_ADDRESS=`kubectl get giteas gitea -o jsonpath='{.status.create_gitea.external_ip_address}'`
git clone https://$GITEA_ADDRESS:3000/networkagent/core -c http.sslVerify=false
git clone https://$GITEA_ADDRESS:3000/networkagent/dublin -c http.sslVerify=false
git clone https://$GITEA_ADDRESS:3000/networkagent/london -c http.sslVerify=false
git clone https://$GITEA_ADDRESS:3000/networkagent/london-cluster -c http.sslVerify=false
git clone https://$GITEA_ADDRESS:3000/networkagent/newyork -c http.sslVerify=false
```



```
export GITEA_ADDRESS=`kubectl get giteas gitea -o jsonpath='{.status.create_gitea.external_ip_address}'`
kpt alpha repo register --namespace default --repo-basic-username=networkagent --repo-basic-password=your_password https://$GITEA_ADDRESS:3000/networkagent/blueprints.git
```