#!/bin/sh
# App on localhost; the Cloudflare Tunnel is the only way in.
set -e
mkdir -p /data
/app/.venv/bin/uvicorn radar.app:app --host 127.0.0.1 --port 8000 &
APP=$!
if [ -n "$CLOUDFLARE_TUNNEL_TOKEN" ]; then
  cloudflared tunnel --no-autoupdate run --token "$CLOUDFLARE_TUNNEL_TOKEN" --url http://127.0.0.1:8000 &
  wait -n $APP $! 2>/dev/null || wait $APP
else
  wait $APP
fi
