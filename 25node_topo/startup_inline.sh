#!/usr/bin/env bash
# EXACT same behavior as your inline cmd; only ETH1_ADDR is parameterized.
set -e

ulimit -n 65536
sysctl -w fs.inotify.max_user_instances=8192
sysctl -w fs.inotify.max_user_watches=524288
sysctl -w fs.file-max=2097152
exec > >(tee /var/log/startup.log) 2>&1

command -v k3s >/dev/null 2>&1 || (
  apt-get update &&
  apt-get install -y curl python3 python3-pip &&
  curl -Lo /usr/local/bin/k3s https://github.com/k3s-io/k3s/releases/download/v1.32.4+k3s1/k3s &&
  chmod +x /usr/local/bin/k3s &&
  pip3 install flask requests kubernetes
)

curl --fail -LS "https://github.com/liqotech/liqo/releases/download/v1.0.0/liqoctl-linux-amd64.tar.gz" | tar -xz
sudo install -o root -g root -m 0755 liqoctl /usr/local/bin/liqoctl

ip link set eth1 up
ip addr add "${ETH1_ADDR}" brd 10.0.1.255 dev eth1
#ip route del default via 172.20.20.1 dev eth0
#ip route add default via 10.0.1.1 dev eth1 
sleep 15
sudo ip link set eth1 mtu 1400

# NOTE: unchanged on purpose (as requested)
k3s server --node-name serf1 --disable traefik --disable-network-policy --snapshotter native &
sleep 15

chmod 666 /etc/rancher/k3s/k3s.yaml
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

echo "Waiting for containerd to be ready..."
sleep 10
until /usr/local/bin/k3s ctr version >/dev/null 2>&1; do
    echo "  containerd not yet ready, retrying..."
    sleep 3
done
echo "Containerd is ready."

echo "Importing cached images into containerd..."
for f in /opt/k3s-images/*.tar; do
    echo "Loading $f ..."
    /usr/local/bin/k3s ctr images import "$f" || true
done

#ip route del default via 10.0.1.1 dev eth1
#ip route add default via 172.20.20.1 dev eth0
k3s kubectl apply -f /tmp/qos-controller-daemonset.yaml
k3s kubectl apply -f /tmp/service-account.yaml
k3s kubectl apply -f /tmp/cluster-role.yaml
k3s kubectl apply -f /tmp/cluster-role-binding.yaml
k3s kubectl apply -f /tmp/deployment-scheduler.yaml
k3s kubectl apply -f /tmp/ram_price.yaml
k3s kubectl apply -f /tmp/storage_price.yaml
k3s kubectl apply -f /tmp/vcpu_price.yaml
k3s kubectl apply -f /tmp/vgpu_price.yaml

sudo mv /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(whoami):$(whoami) ~/.kube/config
sudo chmod 600 ~/.kube/config

sleep infinity
