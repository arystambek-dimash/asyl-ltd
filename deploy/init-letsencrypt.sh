#!/usr/bin/env sh
set -eu

env_file_value() {
  key="$1"
  if [ -f .env ]; then
    awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' .env
  fi
}

domain="${DOMAIN:-$(env_file_value DOMAIN)}"
domain="${domain:-asyl-ltd.kz}"
domains="-d ${domain} -d www.${domain}"
email="${CERTBOT_EMAIL:-$(env_file_value CERTBOT_EMAIL)}"
email="${email:-admin@asyl-ltd.kz}"
staging="${CERTBOT_STAGING:-$(env_file_value CERTBOT_STAGING)}"
staging="${staging:-0}"
rsa_key_size="${CERTBOT_RSA_KEY_SIZE:-$(env_file_value CERTBOT_RSA_KEY_SIZE)}"
rsa_key_size="${rsa_key_size:-4096}"
data_path="./deploy/certbot"
nginx_conf="./deploy/nginx/conf.d/asyl-ltd.conf"
nginx_conf_disabled="./deploy/nginx/conf.d/asyl-ltd.conf.ssl-disabled"
bootstrap_conf="./deploy/nginx/conf.d/00-acme-bootstrap.conf"

if [ "$email" = "admin@asyl-ltd.kz" ]; then
  echo "Set CERTBOT_EMAIL in .env before issuing production certificates." >&2
  exit 1
fi

mkdir -p "${data_path}/conf" "${data_path}/www"

if [ -e "${data_path}/conf/live/${domain}/fullchain.pem" ] && [ -e "${data_path}/conf/renewal/${domain}.conf" ]; then
  echo "Certificate already exists for ${domain}."
  exit 0
fi

restore_nginx_conf() {
  rm -f "$bootstrap_conf"
  if [ -f "$nginx_conf_disabled" ] && [ ! -f "$nginx_conf" ]; then
    mv "$nginx_conf_disabled" "$nginx_conf"
  fi
}

trap restore_nginx_conf EXIT INT TERM

echo "Preparing HTTP-only nginx bootstrap for ACME challenge..."
docker compose -f docker-compose.prod.yml stop nginx >/dev/null 2>&1 || true

if [ -f "$nginx_conf" ]; then
  mv "$nginx_conf" "$nginx_conf_disabled"
fi

cat > "$bootstrap_conf" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${domain} www.${domain};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        try_files \$uri =404;
    }

    location / {
        return 200 "ACME bootstrap for ${domain}\\n";
        add_header Content-Type text/plain;
    }
}
EOF

echo "Starting nginx for ACME challenge..."
docker compose -f docker-compose.prod.yml up -d nginx
rm -rf "${data_path}/conf/live/${domain}"
rm -rf "${data_path}/conf/archive/${domain}"
rm -f "${data_path}/conf/renewal/${domain}.conf"

staging_arg=""
if [ "$staging" != "0" ]; then
  staging_arg="--staging"
fi

echo "Requesting Let's Encrypt certificate for ${domain} and www.${domain}..."
docker compose -f docker-compose.prod.yml run --rm --entrypoint certbot certbot \
  certonly --webroot -w /var/www/certbot \
  $staging_arg \
  --email "$email" \
  --rsa-key-size "$rsa_key_size" \
  --agree-tos \
  --no-eff-email \
  --force-renewal \
  $domains

echo "Restoring HTTPS nginx config..."
docker compose -f docker-compose.prod.yml stop nginx >/dev/null 2>&1 || true
restore_nginx_conf
trap - EXIT INT TERM

echo "Starting nginx and certbot renewal service..."
docker compose -f docker-compose.prod.yml up -d nginx certbot

echo "Certificate ready."
