#!/bin/sh
# Scam SMS Radar on a Linux box that already has Docker (see autocut's
# deploy/server-setup.sh for the Docker install).  Data in /opt/radar/data,
# secrets in /opt/radar/.env, service `radar`.
set -e
sudo mkdir -p /opt/radar/data && sudo chown -R "$USER" /opt/radar && cd /opt/radar
if [ -d src ]; then git -C src pull -q; else git clone -q https://github.com/marknaman05/scam-radar.git src; fi
[ -f .env ] || printf 'TYPESAFE_API_KEY=\nRADAR_REVIEW_TOKEN=\nCLOUDFLARE_TUNNEL_TOKEN=\n' > .env
sudo docker build -q -t radar:latest src
sudo tee /etc/systemd/system/radar.service >/dev/null <<'UNIT'
[Unit]
Description=Scam SMS Radar (web app + Cloudflare Tunnel)
After=docker.service
Requires=docker.service
[Service]
Restart=always
RestartSec=5
ExecStartPre=-/usr/bin/docker rm -f radar
ExecStart=/usr/bin/docker run --name radar --env-file /opt/radar/.env -v /opt/radar/data:/data --memory=300m radar:latest
ExecStop=/usr/bin/docker stop radar
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable --now radar
echo ">> radar started. Logs: sudo docker logs -f radar"
