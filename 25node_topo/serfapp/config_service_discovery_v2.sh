#!/bin/bash/

 python3 service_discovery_v2.py \
    --query-node clab-century-serf2 \
    --geom-url http://172.20.20.7:4040/cluster-status \
    --rtt-threshold-ms 12 \
    --rpc-addr 127.0.0.1:7373 --timeout-s 8 \
    --sort score_per_cpu --limit 10 \
    --http-serve --http-host 0.0.0.0 --http-port 4041 --http-path /hilbert-output \
    --buyer-url http://127.0.0.1:8090/buyer
    

