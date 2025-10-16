#!/bin/bash

python3 service_discovery.py \
    --query-node clab-century-serf2 \
    --geom-url http://172.20.20.7:4040/cluster-status \
    --rtt-threshold-ms 12 \
    --pct-start 0.02 --max-steps 6 \
    --min-cpu 16 --min-ram 32 --min-storage 1000 --min-gpu 1 \
    --budget-per-cpu 3 --budget-per-ram 3 --budget-per-storage 1.5 --budget-per-gpu 10 \
    --min-score-per-cpu 0.1 --min-score-per-ram 0.1 --min-score-per-storage 0.2 --min-score-per-gpu 0.3 \
    --sort score_per_cpu --limit 10 \
    --rpc-addr 127.0.0.1:7373 --timeout-s 8 \
    --http-serve --http-host 0.0.0.0 --http-port 4041 --http-path /hilbert-output
