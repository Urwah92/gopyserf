#!/bin/bash
set +e

i=$1


while ! ip route | grep -q "^default via 10.0.1.1 dev eth1"; do
  echo "Waiting for default route..."
  sleep 1
done

ip link set eth1 mtu 1400

k3s server --node-name serf${i} --node-ip 10.0.1.$((i+10)) --disable traefik --disable-network-policy --snapshotter native &
sleep 15

chmod 666 /etc/rancher/k3s/k3s.yaml
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml


ip route del default via 10.0.1.1 dev eth1
ip route add default via 172.20.20.1 dev eth0


while true; do
  running_pods=$(k3s kubectl get pods -n kube-system \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.phase}{"\n"}{end}' |
    grep -E 'coredns|local-path-provisioner|metrics-server' |
    grep -c 'Running')

  if [ "$running_pods" -eq 3 ]; then
    echo "All required kube-system pods are running."
    break
  else
    echo "Waiting for kube-system pods... ($running_pods/3 ready)"
    sleep 5
  fi
done

liqoctl install k3s --kubeconfig /etc/rancher/k3s/k3s.yaml --cluster-id serf${i}

k3s kubectl apply -f /tmp/qos-controller-daemonset.yaml
k3s kubectl apply -f /tmp/service-account.yaml
k3s kubectl apply -f /tmp/cluster-role.yaml
k3s kubectl apply -f /tmp/cluster-role-binding.yaml
k3s kubectl apply -f /tmp/deployment-scheduler.yaml

mkdir -p ~/.kube
cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
chown $(whoami):$(whoami) ~/.kube/config
chmod 600 ~/.kube/config

