#!/usr/bin/env bash
./buyer \
  -rpc-addr 127.0.0.1:7373 \
  -event buyer.request \
  -ifname eth0 \
  -http-host 0.0.0.0 -http-port 8090 \
  -pause-min 50s -pause-max 50s \
  -lambda-vcpu 2 -lambda-ram 3 -lambda-storage 0 -lambda-vgpu 0
  -budget-min 0 budget-max 5 -score-min 0 -score-max 0
  
#./buyer \
#  -rpc-addr 127.0.0.1:7373 \
#  -event buyer.request \
#  -ifname eth0 \
#  -http-host 0.0.0.0 -http-port 8090 \
#  -manual-json /opt/serfapp/buyer.json
