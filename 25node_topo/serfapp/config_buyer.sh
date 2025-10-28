#!/usr/bin/env bash
./buyer \
  -rpc-addr 127.0.0.1:7373 \
  -event buyer.request \
  -ifname eth0 \
  -http-host 0.0.0.0 -http-port 8090 \
  -pause-min 25s -pause-max 45s \
  -lambda-vcpu 2 -lambda-ram 3 -lambda-storage 5 -lambda-vgpu 1
