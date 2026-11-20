#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu VM (tested target: Oracle Cloud A1, Ubuntu 24.04).
# Run from the repo root:  bash deploy/server-setup.sh
# Safe to run again, it skips whatever is already done.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== firewall (ports 80, 443)"
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 1 -p tcp --dport "$port" -j ACCEPT
  fi
done
if ! sudo iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null; then
  sudo iptables -I INPUT 1 -p udp --dport 443 -j ACCEPT
fi
if command -v netfilter-persistent >/dev/null; then
  sudo netfilter-persistent save
else
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
  sudo netfilter-persistent save
fi

echo "== docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"
# use sudo for the rest of this run, the group change only applies after re-login
DOCKER="sudo docker"

echo "== .env"
if [ -f .env ]; then
  echo ".env already exists, leaving it alone"
else
  read -rp "Domain (e.g. eecs-roomsync.duckdns.org): " domain
  read -rp "Site username [eecs]: " site_user
  site_user=${site_user:-eecs}
  read -rsp "Site password (what you'll give people): " site_pw
  echo
  hash=$($DOCKER run --rm caddy:2.8-alpine caddy hash-password --plaintext "$site_pw")
  grafana_pw=$(openssl rand -hex 12)
  cat > .env <<EOF
DOMAIN=$domain
POSTGRES_PASSWORD=$(openssl rand -hex 16)
GRAFANA_ADMIN_PASSWORD=$grafana_pw
BASIC_AUTH_USER=$site_user
BASIC_AUTH_HASH='$hash'
EOF
  chmod 600 .env
  echo "wrote .env"
  echo "grafana login is admin / $grafana_pw  (also saved in .env)"
fi

echo "== checking the domain points here"
domain=$(grep '^DOMAIN=' .env | cut -d= -f2)
my_ip=$(curl -fsS https://api.ipify.org || true)
dns_ip=$(getent hosts "$domain" | awk '{print $1}' | head -n1 || true)
echo "this server: ${my_ip:-unknown}   $domain -> ${dns_ip:-nothing}"
if [ -n "$my_ip" ] && [ "$my_ip" != "$dns_ip" ]; then
  echo "WARNING: $domain doesn't point at this server yet. HTTPS won't work until it does."
fi

echo "== starting"
$DOCKER compose -f docker-compose.prod.yml up -d --build
$DOCKER compose -f docker-compose.prod.yml ps

echo
echo "done. open https://$domain (give it a minute for the certificate)"
