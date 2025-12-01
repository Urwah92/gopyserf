SRC=./service_discovery_v6.py

for i in $(seq 4 10); do
  cname="clab-century-serf$i"
  echo "→ $cname"

  # make sure the target directory exists
  docker exec "$cname" mkdir -p /opt/serfapp

  # copy the file
  docker cp "$SRC" "$cname":/opt/serfapp/

  # quick sanity check inside the container (optional)
  docker exec "$cname" bash -lc "ls -lh /opt/serfapp/service_discovery_v5.py"
done
