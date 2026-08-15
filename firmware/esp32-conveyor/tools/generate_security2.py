#!/usr/bin/env python3
"""Generate unique ESP-IDF Security2 material without printing secrets."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--idf-path", type=Path, required=True)
    parser.add_argument("--device-name", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sdkconfig-output", type=Path, required=True)
    parser.add_argument("--username")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    esp_prov_tools = args.idf_path / "tools" / "esp_prov"
    security_tools = esp_prov_tools / "security"
    if not (security_tools / "srp6a.py").is_file():
        raise SystemExit("ESP-IDF Security2 generator was not found")
    sys.path.insert(0, str(esp_prov_tools))
    sys.path.insert(0, str(security_tools))
    from srp6a import generate_salt_and_verifier  # type: ignore[import-not-found]

    username = args.username or f"asyl-{args.device_name.rsplit('-', 1)[-1].lower()}"
    password = secrets.token_urlsafe(24)
    while True:
        salt, verifier = generate_salt_and_verifier(username, password, 16)
        if len(salt) == 16 and len(verifier) == 384:
            break

    credentials_path = args.output_dir / "provisioning-credentials.json"
    qr_path = args.output_dir / "provisioning-qr.json"
    credentials = {
        "device_name": args.device_name,
        "security": 2,
        "username": username,
        "password": password,
    }
    qr_payload = {
        "ver": "v1",
        "name": args.device_name,
        "username": username,
        "pop": password,
        "transport": "ble",
        "security": 2,
    }
    sdkconfig = (
        f'CONFIG_ASYL_PROV_SEC2_SALT_HEX="{salt.hex()}"\n'
        f'CONFIG_ASYL_PROV_SEC2_VERIFIER_HEX="{verifier.hex()}"\n'
    )
    write_private(credentials_path, json.dumps(credentials, indent=2) + "\n")
    try:
        write_private(qr_path, json.dumps(qr_payload, separators=(",", ":")) + "\n")
        write_private(args.sdkconfig_output, sdkconfig)
    except BaseException:
        credentials_path.unlink(missing_ok=True)
        qr_path.unlink(missing_ok=True)
        raise

    print(f"Security2 files created in {args.output_dir}")
    print(f"Device sdkconfig defaults created at {args.sdkconfig_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
