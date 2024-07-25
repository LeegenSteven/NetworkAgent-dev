# Edge Appliance Virtual Machine

A virtual machine running a [VyOS](https://vyos.net/) docker container and additional monitoring software is created as follows.


## Create a VM

Create a Debian/Ubuntu virtual machine in GCP and install Docker to the VM


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
docker run -d --rm --name vyos --network host --privileged -v /lib/modules:/lib/modules vyos:1.4 /sbin/init
docker exec -ti vyos /bin/bash
```

## Save the Virtual Machine Image


