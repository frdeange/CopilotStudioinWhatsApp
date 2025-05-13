#!/usr/bin/env bash
# Simple helper to expose local FastAPI (http://localhost:8000) via ngrok and print the public URL.

set -euo pipefail

# Requires 'ngrok' binary in PATH and AUTHTOKEN configured (ngrok config add‑authtoken <token>)

# Check if NGROCK_AUTH_TOKEN is set in .env
ENV_FILE="$(dirname "$0")/../.env"
if [ -f "$ENV_FILE" ]; then
  NGROCK_AUTH_TOKEN=$(grep '^NGROCK_AUTH_TOKEN=' "$ENV_FILE" | cut -d'=' -f2- | tr -d '"')
else
  NGROCK_AUTH_TOKEN=""
fi

if [ -z "$NGROCK_AUTH_TOKEN" ]; then
  echo "🔑 NGROCK_AUTH_TOKEN not found in .env."
  echo "Please get your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken"
  read -rp "Enter your ngrok authtoken: " NGROCK_AUTH_TOKEN
  # Update or add NGROCK_AUTH_TOKEN in .env
  if [ -f "$ENV_FILE" ]; then
    if grep -q '^NGROCK_AUTH_TOKEN=' "$ENV_FILE"; then
      sed -i "s|^NGROCK_AUTH_TOKEN=.*|NGROCK_AUTH_TOKEN=\"$NGROCK_AUTH_TOKEN\"|" "$ENV_FILE"
    else
      echo "NGROCK_AUTH_TOKEN=\"$NGROCK_AUTH_TOKEN\"" >> "$ENV_FILE"
    fi
    echo "✅ NGROCK_AUTH_TOKEN updated in $ENV_FILE"
  else
    echo "NGROCK_AUTH_TOKEN=\"$NGROCK_AUTH_TOKEN\"" > "$ENV_FILE"
    echo "✅ .env created and NGROCK_AUTH_TOKEN set"
  fi
fi

# Ensure ngrok is installed
if ! command -v ngrok >/dev/null; then
  echo "🔄 ngrok not found, installing..."
  bash "$(dirname "$0")/install_ngrock.sh"
fi

# Check if ngrok is already configured with the authtoken
NGROK_CONFIG="$HOME/.ngrok2/ngrok.yml"
if [ ! -f "$NGROK_CONFIG" ] || ! grep -q "$NGROCK_AUTH_TOKEN" "$NGROK_CONFIG"; then
  echo "🔑 Configuring ngrok with your authtoken..."
  ngrok config add-authtoken "$NGROCK_AUTH_TOKEN"
fi

PORT=${1:-8000}

ngrok http $PORT > /dev/null &
NGROK_PID=$!

sleep 2
PUBLIC_URL=$(curl --silent http://127.0.0.1:4040/api/tunnels | jq -r '.tunnels[0].public_url')

echo "✅ Ngrok tunnel established: $PUBLIC_URL"

# Update CALLBACK_URI_HOST in .env
if [ -f "$ENV_FILE" ]; then
  if grep -q '^CALLBACK_URI_HOST=' "$ENV_FILE"; then
    sed -i -E "s|^CALLBACK_URI_HOST=.*|CALLBACK_URI_HOST=\"$PUBLIC_URL\"|" "$ENV_FILE"
  else
    echo "CALLBACK_URI_HOST=\"$PUBLIC_URL\"" >> "$ENV_FILE"
  fi
  echo "🔄 Updated CALLBACK_URI_HOST in $ENV_FILE"
else
  echo "⚠️ .env file not found at $ENV_FILE, please update CALLBACK_URI_HOST manually."
fi

echo "Remember to check CALLBACK_URI_HOST=$PUBLIC_URL in your .env before running the bot. This app should update it automatically."

wait $NGROK_PID