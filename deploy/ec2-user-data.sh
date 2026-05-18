#!/usr/bin/env bash
set -euo pipefail

APP_DOMAIN="${APP_DOMAIN:-cipher-collab.duckdns.org}"
LEGACY_SERVER_NAME="${LEGACY_SERVER_NAME:-ec2-16-171-161-189.eu-north-1.compute.amazonaws.com}"
BACKEND_REPO="https://github.com/dikshakarwasra/cipher-colab-backend.git"
FRONTEND_REPO="https://github.com/dikshakarwasra/cipher-colab-frontend.git"
BACKEND_DIR="/app/backend"
FRONTEND_DIR="/app/frontend"
DUCKDNS_DOMAIN="${DUCKDNS_DOMAIN:-cipher-collab}"
DUCKDNS_TOKEN="${DUCKDNS_TOKEN:-REPLACE_WITH_DUCKDNS_TOKEN}"

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y ca-certificates curl gnupg git nginx python3 python3-venv python3-pip certbot python3-certbot-nginx openssl

if ! command -v node >/dev/null 2>&1 || ! node --version | grep -q '^v22\.'; then
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list
  apt-get update
  apt-get install -y nodejs
fi

npm install -g pm2

mkdir -p /app

if [ -d "$BACKEND_DIR/.git" ]; then
  git -C "$BACKEND_DIR" pull --ff-only
else
  rm -rf "$BACKEND_DIR"
  git clone "$BACKEND_REPO" "$BACKEND_DIR"
fi

if [ -d "$FRONTEND_DIR/.git" ]; then
  git -C "$FRONTEND_DIR" pull --ff-only
else
  rm -rf "$FRONTEND_DIR"
  git clone "$FRONTEND_REPO" "$FRONTEND_DIR"
fi

JWT_ACCESS_SECRET="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(48)).decode())')"
JWT_REFRESH_SECRET="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(48)).decode())')"
AES_GCM_KEY="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"

cat > "$BACKEND_DIR/.env" <<EOF
APP_ENV=production
DATABASE_URL=sqlite+aiosqlite:////app/backend/cipher_collab.db
JWT_ACCESS_SECRET=${JWT_ACCESS_SECRET}
JWT_REFRESH_SECRET=${JWT_REFRESH_SECRET}
AES_GCM_KEY=${AES_GCM_KEY}
CORS_ORIGINS=["https://${APP_DOMAIN}","http://${APP_DOMAIN}"]
FRONTEND_URL=https://${APP_DOMAIN}
EOF

cat > "$FRONTEND_DIR/.env" <<EOF
VITE_API_BASE_URL=/api/v1
VITE_WS_BASE_URL=wss://${APP_DOMAIN}/ws
EOF
cp "$FRONTEND_DIR/.env" "$FRONTEND_DIR/client/.env"

python3 -m venv "$BACKEND_DIR/.venv"
"$BACKEND_DIR/.venv/bin/python" -m pip install --upgrade pip
"$BACKEND_DIR/.venv/bin/python" -m pip install -r "$BACKEND_DIR/requirements.txt"
cd "$BACKEND_DIR"
"$BACKEND_DIR/.venv/bin/python" -m alembic upgrade head || true
"$BACKEND_DIR/.venv/bin/python" -c "from app.main import app; print(app.title)"

cat > /etc/systemd/system/cipher-colab-backend.service <<EOF
[Unit]
Description=Cipher Colab FastAPI backend
After=network.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=${BACKEND_DIR}
Environment=PYTHONPATH=${BACKEND_DIR}
ExecStart=${BACKEND_DIR}/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 5000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cipher-colab-backend
systemctl restart cipher-colab-backend

for attempt in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:5000/health >/tmp/cipher-colab-health.json; then
    break
  fi
  sleep 2
done

if ! curl -fsS http://127.0.0.1:5000/health >/dev/null; then
  journalctl -u cipher-colab-backend -n 120 --no-pager || true
fi

cd "$FRONTEND_DIR"
if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi
npm run build

cat > /etc/nginx/sites-available/cipher-colab <<EOF
server {
    listen 80;
    server_name ${APP_DOMAIN};

    root ${FRONTEND_DIR}/dist/public;
    index index.html;

    location /api/v1/ {
        proxy_pass http://127.0.0.1:5000/api/v1/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:5000/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}

server {
    listen 80 default_server;
    server_name ${LEGACY_SERVER_NAME} _;
    return 301 https://${APP_DOMAIN}\$request_uri;
}
EOF

ln -sfn /etc/nginx/sites-available/cipher-colab /etc/nginx/sites-enabled/cipher-colab
rm -f /etc/nginx/sites-enabled/default
printf 'server_names_hash_bucket_size 128;\n' > /etc/nginx/conf.d/server_names_hash_bucket_size.conf
nginx -t
systemctl reload nginx

if [ "$DUCKDNS_TOKEN" != "REPLACE_WITH_DUCKDNS_TOKEN" ]; then
  mkdir -p /home/ubuntu/duckdns
  curl -fsSk "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&ip=" -o /home/ubuntu/duckdns/duck.log
  chown -R ubuntu:ubuntu /home/ubuntu/duckdns
fi

if [ ! -d "/etc/letsencrypt/live/${APP_DOMAIN}" ]; then
  certbot --nginx -d "$APP_DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect || true
fi

systemctl reload nginx
systemctl status cipher-colab-backend --no-pager || true

# FIXED: keeps CORS origins JSON-compatible for pydantic-settings
