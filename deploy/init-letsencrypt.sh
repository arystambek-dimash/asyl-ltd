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

if [ "$email" = "admin@asyl-ltd.kz" ]; then
  echo "Set CERTBOT_EMAIL in .env before issuing production certificates." >&2
  exit 1
fi

mkdir -p "${data_path}/conf/live/${domain}" "${data_path}/www"

if [ -e "${data_path}/conf/live/${domain}/fullchain.pem" ]; then
  echo "Certificate already exists for ${domain}."
  exit 0
fi

echo "Creating temporary self-signed certificate for nginx bootstrap..."
openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
  -keyout "${data_path}/conf/live/${domain}/privkey.pem" \
  -out "${data_path}/conf/live/${domain}/fullchain.pem" \
  -subj "/CN=${domain}" >/dev/null 2>&1

echo "Starting nginx with temporary certificate..."
docker compose -f docker-compose.prod.yml up -d nginx

echo "Removing temporary certificate..."
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

echo "Reloading nginx..."
docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload

echo "Certificate ready."
