# WireGuard tunnel for camera access

Goal: connect an operator computer to the server over WireGuard and access camera
IPs through the VPN without routing all internet traffic through the server.

Server:

- IPv4: `78.40.109.240`
- IPv6: `2a00:5da0:1000:1::3754`
- WireGuard UDP port: `51820`
- VPN subnet: `10.13.13.0/24`

## 1. Configure `.env`

On the server, set these values in `.env`:

```env
WG_SERVERURL=78.40.109.240
WG_SERVERPORT=51820
WG_PEERS=operator-laptop
WG_PEERDNS=1.1.1.1
WG_INTERNAL_SUBNET=10.13.13.0
WG_ALLOWEDIPS=10.13.13.0/24,192.168.1.0/24
WG_PERSISTENTKEEPALIVE_PEERS=all
```

Replace `192.168.1.0/24` with the real LAN subnet where the cameras are
reachable from the Docker host. Examples:

- Cameras `192.168.1.20`, `192.168.1.21` -> `192.168.1.0/24`
- Cameras `10.0.0.50`, `10.0.0.51` -> `10.0.0.0/24`

To add more operator devices, extend `WG_PEERS`:

```env
WG_PEERS=operator-laptop,manager-laptop,phone
```

## 2. Open the VPN port on the server

```bash
sudo ufw allow 51820/udp
```

If the host uses a cloud firewall, open UDP `51820` there too.

## 3. Enable forwarding on the host

```bash
printf 'net.ipv4.ip_forward=1\n' | sudo tee /etc/sysctl.d/99-wireguard-camera-tunnel.conf
sudo sysctl --system
```

## 4. Start WireGuard

```bash
docker compose up -d wireguard
docker compose logs -f wireguard
```

The peer config is generated under:

```text
config/peer_operator-laptop/peer_operator-laptop.conf
```

If `config/` was generated earlier with the wrong `SERVERURL` or `ALLOWEDIPS`,
stop WireGuard and regenerate the peer configs after backing up any configs that
are already in use.

## 5. Connect the operator computer

Install the WireGuard app on the operator computer and import:

```text
config/peer_operator-laptop/peer_operator-laptop.conf
```

After connecting, test:

```bash
ping 10.13.13.1
ping <camera-ip>
curl -I http://<camera-ip>
```

Only `10.13.13.0/24` and the camera LAN from `WG_ALLOWEDIPS` should go through
the tunnel. Regular internet traffic stays on the operator computer's network.

## 6. Camera access notes

This setup assumes the Docker host can already reach the cameras directly on the
LAN. If the public server is not in the same LAN as the cameras, a second
WireGuard peer must run on a small gateway computer inside the camera LAN, and
the server must route the camera subnet to that gateway peer.
