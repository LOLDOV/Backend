#!/usr/bin/env bash
# Oracle Cloud Ubuntu 24.04 서버에서 처음 한 번 실행한다:  bash setup.sh <공인IP>
# 끝나면 AI/.env 를 따로 복사하고 (scp), Oracle 콘솔 Security List 에 TCP 80,443 수신 규칙을 추가한다.
set -euo pipefail
IP=${1:?usage: bash setup.sh <public-ip>}
HOST="${IP//./-}.sslip.io"
APP=/home/ubuntu/loldov

sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg python3-venv git libgl1 libglib2.0-0 libgomp1 \
  debian-keyring debian-archive-keyring apt-transport-https curl netfilter-persistent

mkdir -p "$APP" && cd "$APP"
for r in AI Backend Frontend; do [ -d "$r" ] || git clone -q "https://github.com/LOLDOV/$r"; done
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r Backend/deploy/oracle/requirements.txt

sudo cp Backend/deploy/oracle/loldov.service /etc/systemd/system/loldov.service
sudo systemctl daemon-reload
sudo systemctl enable --now loldov

# Caddy: HTTPS 인증서를 자동으로 받아 8000 포트 앱으로 연결
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy
printf '%s {\n\treverse_proxy 127.0.0.1:8000\n}\n' "$HOST" | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl reload caddy

# Oracle Ubuntu 이미지는 iptables 가 22번만 열어 둔다
for p in 443 80; do
  sudo iptables -C INPUT -p tcp --dport $p -m state --state NEW -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 5 -p tcp --dport $p -m state --state NEW -j ACCEPT
done
sudo netfilter-persistent save

echo "done: https://$HOST  (AI/.env 복사, Security List 80/443 열기 필요)"
