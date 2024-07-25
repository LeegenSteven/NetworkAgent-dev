# Edge Appliance Virtual Machine

A virtual machine running a [VyOS](https://vyos.net/) docker container and additional monitoring software is created as follows.


## Create a VM

Create a Debian/Ubuntu virtual machine in GCP. 

SSH onto the VM and install Docker.

```
# Add Docker's official GPG key:
sudo apt-get update
sudo apt-get install ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repository to Apt sources:
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```


## Build VyOS Docker Image

Build a VyOS Docker Image

```
mkdir vyos
cd vyos
wget https://github.com/vyos/vyos-rolling-nightly-builds/releases/download/1.5-rolling-202407171706/vyos-1.5-rolling-202407171706-amd64.iso
mkdir rootfs
mkdir unsquashfs
sudo mount -o loop vyos-1.5-rolling-202407171706-amd64.iso rootfs
sudo apt-get install -y squashfs-tools
sudo unsquashfs -f -d unsquashfs/ rootfs/live/filesystem.squashfs
sudo tar -C unsquashfs -c . | docker import - vyos:1.5
```

## Test the VyOS Docker image

Run the vyos container in a VM

```
docker run -d --rm --name vyos --network host --privileged -v /lib/modules:/lib/modules vyos:1.5 /sbin/init
docker exec -ti vyos /bin/bash
```

## Create SSH keys

Add local key to project

```
ssh-keygen -o -a 100 -t ed25519 -f google-compute -C briannaughton
gcloud compute os-login ssh-keys add --key-file=google-compute.pub --project=free5gc-384814 --ttl=1d
```
