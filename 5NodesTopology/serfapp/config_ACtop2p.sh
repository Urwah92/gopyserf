#!/bin/bash

./ACp2p_v2 \
  -offer-url http://localhost:8080/resource_offer \
  -serf ./serf \
  -rpc-addr 127.0.0.1:7373 \
  -http-serve \
  -http-host 0.0.0.0 \
  -http-port 4042 \
  -http-path /members \
  -members-file ./sellers.json \
  -buyers-file ./buy.json \
  -interval 50s \
  -health-interval 5s



