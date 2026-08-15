#!/usr/bin/env python3
"""Provision ASYL Wi-Fi and API credentials over ESP-IDF's encrypted BLE link."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.util
import json
import os
import re
import stat
import sys
import uuid
from pathlib import Path
from types import ModuleType

TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Securely provision an ASYL ESP32 without placing Wi-Fi, BLE, or "
            "device API secrets in the shell command line."
        )
    )
    parser.add_argument(
        "--idf-path",
        type=Path,
        default=Path(os.environ["IDF_PATH"]) if "IDF_PATH" in os.environ else None,
        required="IDF_PATH" not in os.environ,
    )
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--device-id")
    parser.add_argument("--ssid")
    return parser.parse_args()


def read_private_credentials(path: Path) -> dict[str, object]:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError as exc:
        raise SystemExit(f"Credentials file does not exist: {path}") from exc
    if mode & 0o077:
        raise SystemExit(
            f"Refusing credentials file with group/other permissions {mode:o}; "
            f"run: chmod 600 {path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Cannot read credentials file: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("Credentials file must contain a JSON object")
    required = {"device_name", "security", "username", "password"}
    if set(payload) != required or payload.get("security") != 2:
        raise SystemExit("Credentials file is not an ASYL Security2 credential file")
    for key in ("device_name", "username", "password"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise SystemExit(f"Credentials field {key!r} is invalid")
    return payload


def canonical_uuid4(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise SystemExit("Device ID must be a canonical UUIDv4") from exc
    canonical = str(parsed)
    if parsed.version != 4 or value != canonical:
        raise SystemExit("Device ID must be a canonical lowercase UUIDv4")
    return canonical


def load_official_client(idf_path: Path) -> ModuleType:
    client_path = idf_path / "tools" / "esp_prov" / "esp_prov.py"
    if not client_path.is_file():
        raise SystemExit(f"Official ESP-IDF provisioning client not found: {client_path}")
    os.environ["IDF_PATH"] = str(idf_path)
    sys.path.insert(0, str(client_path.parent))
    spec = importlib.util.spec_from_file_location("asyl_esp_prov", client_path)
    if spec is None or spec.loader is None:
        raise SystemExit("Cannot load the official ESP-IDF provisioning client")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "ESP-IDF BLE provisioning dependencies are missing. Run "
            "'bash \"$IDF_PATH/install.sh\" --enable-pytest', then source "
            "export.sh."
        ) from exc
    return module


def main() -> int:
    args = parse_args()
    credentials = read_private_credentials(args.credentials)
    device_id = canonical_uuid4(args.device_id or input("Backend device UUID: ").strip())
    ssid = args.ssid or input("Wi-Fi SSID: ").strip()
    if not ssid or len(ssid.encode("utf-8")) > 32:
        raise SystemExit("Wi-Fi SSID must be 1..32 bytes")

    device_token = getpass.getpass("One-time backend device token: ").strip()
    if not TOKEN_PATTERN.fullmatch(device_token):
        raise SystemExit("Device token must be 43 base64url characters")
    wifi_password = getpass.getpass("Wi-Fi password: ")
    if len(wifi_password.encode("utf-8")) > 64:
        raise SystemExit("Wi-Fi password must be at most 64 bytes")

    custom_data = json.dumps(
        {
            "base_url": "https://asyl-ltd.kz/api",
            "device_id": device_id,
            "token": device_token,
        },
        separators=(",", ":"),
    )
    client = load_official_client(args.idf_path.resolve())
    original_argv = sys.argv
    try:
        # Secrets are injected only into the already-running process. They do
        # not appear in shell history or the operating system's process list.
        sys.argv = [
            str(args.idf_path / "tools" / "esp_prov" / "esp_prov.py"),
            "--transport",
            "ble",
            "--service_name",
            str(credentials["device_name"]),
            "--sec_ver",
            "2",
            "--sec2_username",
            str(credentials["username"]),
            "--sec2_pwd",
            str(credentials["password"]),
            "--custom_data",
            custom_data,
            "--ssid",
            ssid,
            "--passphrase",
            wifi_password,
        ]
        asyncio.run(client.main())
    finally:
        sys.argv = original_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
