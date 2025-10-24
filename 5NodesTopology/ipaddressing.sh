#!/bin/sh

for i in $(seq 1 5)
do
  container="clab-century-serf$i"
  ip_address="10.0.1.$((10 + i))"

  echo "[INFO] Configuring $container -> $ip_address"

  # Bring up eth1 and assign IP
  sudo docker exec -d "$container" ip link set eth1 up
  sudo docker exec -d "$container" ip addr add "$ip_address"/24 brd 10.0.1.255 dev eth1

  # Create node.json inside /opt/serfapp/
  sudo docker exec -i "$container" sh -c "cat > /opt/serfapp/node.json" <<EOF
{
  "node_name": "$container",
  "bind": "0.0.0.0:7946",
  "advertise": "$ip_address:7946",
  "rpc_addr": "0.0.0.0:7373"
}
EOF

done

echo "[INFO] IP addressing setup complete ✅"



