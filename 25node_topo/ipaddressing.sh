#!/bin/sh
set -e

LAB="century"   # <-- must match the 'name:' in your topology

for i in $(seq 1 25); do
  container="clab-${LAB}-serf${i}"

  if [ "$i" -le 12 ]; then
    net="10.0.1"
    host=$((10 + i))            # 1->11 ... 12->22
    brd="10.0.1.255"
  else
    net="10.0.2"
    host=$((10 + (i - 12)))     # 13->11 ... 25->23
    brd="10.0.2.255"
  fi

  ip_address="${net}.${host}"

  echo "[INFO] Configuring ${container} -> ${ip_address}/24 on eth1"

  # Bring up eth1 and assign IP
  sudo docker exec -d "$container" ip link set eth1 up
  sudo docker exec -d "$container" ip addr add "${ip_address}/24" brd "$brd" dev eth1 || true

  # Add the required static route per group
  if [ "$i" -le 12 ]; then
    # serf1..serf12: reach 10.0.2.0/25 via 10.0.1.1
    sudo docker exec -d "$container" ip route add 10.0.2.0/25 via 10.0.1.1 dev eth1 || true
  else
    # serf13..serf25: reach 10.0.1.0/24 via 10.0.2.1
    sudo docker exec -d "$container" ip route add 10.0.1.0/24 via 10.0.2.1 dev eth1 || true
  fi

  # Create node.json inside /opt/serfapp/
  sudo docker exec -i "$container" sh -lc "cat > /opt/serfapp/node.json" <<EOF
{
  "node_name": "serf${i}",
  "bind": "0.0.0.0:7946",
  "advertise": "${ip_address}:7946",
  "rpc_addr": "0.0.0.0:7373"
}
EOF

done

echo "[INFO] IP addressing + static routes configured ✅"

