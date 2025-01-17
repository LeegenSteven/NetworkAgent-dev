#!/bin/bash
#
# Configure iptables in UPF
#
iptables -t nat -A POSTROUTING -o ens5  -j MASQUERADE
iptables -I FORWARD 1 -j ACCEPT

