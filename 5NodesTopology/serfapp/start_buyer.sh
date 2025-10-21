#!/usr/bin/env bash
./buyer \
  -rpc-addr 127.0.0.1:7373 \
  -event buyer.request \
  -ip 10.0.1.14 \
  -http-host 0.0.0.0 \
  -http-port 8090 \
  -busy-min 30s -busy-max 60s \
  -idle-min 20s -idle-max 40s \
  -emit-mean 5s \
  -lambda-vcpu 2 -lambda-ram 3 -lambda-storage 5 -lambda-vgpu 1