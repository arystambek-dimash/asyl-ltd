# Production deploy for asyl-ltd.kz

Server:

- IPv4: `78.40.109.240`
- IPv6: `2a00:5da0:1000:1::3754`
- User: `ubuntu`
- Domain: `asyl-ltd.kz`

## 1. DNS

Create DNS records before issuing Let's Encrypt certificates:

```text
A     asyl-ltd.kz      78.40.109.240
A     www.asyl-ltd.kz  78.40.109.240
AAAA  asyl-ltd.kz      2a00:5da0:1000:1::3754
AAAA  www.asyl-ltd.kz  2a00:5da0:1000:1::3754
```

If IPv6 is not fully routed on the server, skip `AAAA` until IPv6 is verified.

## 2. Pull code on the server

```bash
ssh ubuntu@78.40.109.240
cd ~/asyl-ltd
git pull
```

If the repo is not cloned yet:

```bash
git clone <repo-url> ~/asyl-ltd
cd ~/asyl-ltd
```

## 3. Create production `.env`

```bash
cp .env.example .env
nano .env
```

Set at least:

```env
DOMAIN=asyl-ltd.kz
CERTBOT_EMAIL=<real-admin-email>
SECRET_KEY=<long-random-secret>
POSTGRES_PASSWORD=<strong-db-password>
SUPER_ADMIN_EMAIL=<admin-email>
SUPER_ADMIN_PASS=<strong-admin-password>
ALLOWED_HOSTS=asyl-ltd.kz,www.asyl-ltd.kz,78.40.109.240
CORS_ALLOWED_ORIGINS=https://asyl-ltd.kz,https://www.asyl-ltd.kz
CSRF_TRUSTED_ORIGINS=https://asyl-ltd.kz,https://www.asyl-ltd.kz
NEXT_PUBLIC_API_URL=/api
```

For WireGuard camera access, set the real camera LAN subnet:

```env
WG_ALLOWEDIPS=10.13.13.0/24,192.168.1.0/24
```

Replace `192.168.1.0/24` with the actual camera subnet.

For the dashboard camera streams (go2rtc), the server must be in the same
Tailscale tailnet as the camera PC (`tailscale ping 100.109.156.107`), and
`.env` must contain the MediaMTX read-only credentials:

```env
CAMERA_HOST=100.109.156.107
CAMERA_USER=viewer
CAMERA_PASS=<viewer-password>
```

## 4. Open firewall

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 51820/udp
sudo ufw reload
```

Enable forwarding for WireGuard:

```bash
printf 'net.ipv4.ip_forward=1\n' | sudo tee /etc/sysctl.d/99-asyl-wireguard.conf
sudo sysctl --system
```

## 5. First certificate issue

Make the script executable:

```bash
chmod +x deploy/init-letsencrypt.sh deploy/backup/backup.sh
```

Run the app services and bootstrap certificate:

```bash
docker compose -f docker-compose.prod.yml up -d db redis backend frontend
./deploy/init-letsencrypt.sh
docker compose -f docker-compose.prod.yml up -d
```

After that, the app should be public:

```text
https://asyl-ltd.kz
```

## 6. Backup

`db-backup` runs every Sunday at `03:00` container time and keeps one latest dump:

```text
backups/asyl-latest.dump
```

Manual backup:

```bash
docker compose -f docker-compose.prod.yml exec db-backup /bin/sh /backup/backup.sh
```

Restore example:

```bash
docker compose -f docker-compose.prod.yml exec -T db pg_restore \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists \
  < backups/asyl-latest.dump
```

## 7. CI/CD

Production auto-deploy is defined in:

```text
.github/workflows/deploy-production.yml
```

It runs on every push to `main` and executes this script on the server:

```text
deploy/remote-deploy.sh
```

The workflow connects to `ubuntu@78.40.109.240` by SSH key. Add this GitHub
Actions repository secret:

```text
PROD_SSH_KEY
```

Optional override secrets:

```text
PROD_HOST
PROD_PORT
PROD_USER
```

Manual secret setup with GitHub CLI:

```bash
gh secret set PROD_SSH_KEY < config/github-actions-deploy-key
```

The private key must stay out of git. The `config/` directory is ignored.

## 8. WireGuard peer config

Generated configs are under:

```text
config/peer_operatorlaptop/peer_operatorlaptop.conf
```

Import that file into the WireGuard client on the remote computer.
