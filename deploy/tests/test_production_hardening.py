from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = REPO_ROOT / "deploy" / "backup" / "backup.sh"
REMOTE_DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "remote-deploy.sh"
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class BackupRotationTests(unittest.TestCase):
    def test_rotated_manifests_validate_the_prev_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            _write_executable(
                fake_bin / "pg_dump",
                """#!/bin/sh
set -eu
output=
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-f" ]; then
    output="$2"
    shift 2
  else
    shift
  fi
done
printf '%s\\n' "${FAKE_DUMP_CONTENT:-dump}" >"$output"
""",
            )
            _write_executable(
                fake_bin / "pg_restore",
                "#!/bin/sh\nexit 0\n",
            )
            _write_executable(
                fake_bin / "sha256sum",
                """#!/usr/bin/env python3
import hashlib
import pathlib
import sys

if sys.argv[1:2] == ["-c"]:
    valid = True
    for line in pathlib.Path(sys.argv[2]).read_text().splitlines():
        expected, filename = line.split(maxsplit=1)
        actual = hashlib.sha256(pathlib.Path(filename).read_bytes()).hexdigest()
        valid = valid and actual == expected
    raise SystemExit(0 if valid else 1)

for filename in sys.argv[1:]:
    digest = hashlib.sha256(pathlib.Path(filename).read_bytes()).hexdigest()
    print(f"{digest}  {filename}")
""",
            )

            media_root = root / "media"
            media_root.mkdir()
            media_file = media_root / "upload.txt"
            backup_file = root / "asyl-latest.dump"
            media_backup_file = root / "media-latest.tar.gz"
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
                    "BACKUP_FILE": str(backup_file),
                    "MEDIA_BACKUP_FILE": str(media_backup_file),
                    "MEDIA_ROOT": str(media_root),
                }
            )

            media_file.write_text("first media", encoding="utf-8")
            environment["FAKE_DUMP_CONTENT"] = "first database"
            subprocess.run(
                ["/bin/sh", str(BACKUP_SCRIPT)],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            media_file.write_text("second media", encoding="utf-8")
            environment["FAKE_DUMP_CONTENT"] = "second database"
            subprocess.run(
                ["/bin/sh", str(BACKUP_SCRIPT)],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            previous_database = Path(f"{backup_file}.prev")
            previous_media = Path(f"{media_backup_file}.prev")
            self.assertEqual(
                previous_database.read_text(encoding="utf-8"),
                "first database\n",
            )
            self.assertEqual(
                backup_file.read_text(encoding="utf-8"),
                "second database\n",
            )
            for archive in (
                backup_file,
                previous_database,
                media_backup_file,
                previous_media,
            ):
                manifest = Path(f"{archive}.sha256")
                expected, manifest_path = (
                    manifest.read_text(encoding="utf-8").strip().split(maxsplit=1)
                )
                self.assertEqual(manifest_path, str(archive))
                self.assertEqual(
                    expected,
                    hashlib.sha256(archive.read_bytes()).hexdigest(),
                )
                subprocess.run(
                    [str(fake_bin / "sha256sum"), "-c", str(manifest)],
                    check=True,
                )


class RemoteDeployTests(unittest.TestCase):
    def _environment(
        self,
        root: Path,
        *,
        running_services: str,
        backup_status: int = 0,
    ) -> tuple[dict[str, str], Path]:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        _write_executable(fake_bin / "git", "#!/bin/sh\nexit 0\n")
        _write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
        _write_executable(
            fake_bin / "docker",
            """#!/bin/sh
set -eu
printf '%s\\n' "$*" >>"$FAKE_DOCKER_LOG"
case "$*" in
  *" ps --services --filter status=running")
    printf '%s\\n' "${FAKE_RUNNING_SERVICES:-}"
    ;;
  *" exec -T db-backup /backup/backup.sh")
    exit "${FAKE_BACKUP_STATUS:-0}"
    ;;
esac
exit 0
""",
        )
        app_dir = root / "app"
        cleanup = app_dir / "deploy" / "maintenance" / "cleanup-docker.sh"
        cleanup.parent.mkdir(parents=True)
        _write_executable(cleanup, "#!/bin/sh\nexit 0\n")
        docker_log = root / "docker.log"
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
                "APP_DIR": str(app_dir),
                "LOCK_FILE": str(root / "deploy.lock"),
                "BACKEND_IMAGE_REF": (
                    "ghcr.io/arystambek-dimash/asyl-ltd-backend@sha256:" + "a" * 64
                ),
                "FRONTEND_IMAGE_REF": (
                    "ghcr.io/arystambek-dimash/asyl-ltd-frontend@sha256:" + "b" * 64
                ),
                "FAKE_DOCKER_LOG": str(docker_log),
                "FAKE_RUNNING_SERVICES": running_services,
                "FAKE_BACKUP_STATUS": str(backup_status),
            }
        )
        return environment, docker_log

    def test_deploy_refuses_when_backup_service_is_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, docker_log = self._environment(
                Path(temporary),
                running_services="",
            )
            result = subprocess.run(
                ["/bin/sh", str(REMOTE_DEPLOY_SCRIPT)],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("without a running db-backup", result.stderr)
            self.assertNotIn(" pull ", docker_log.read_text(encoding="utf-8"))

    def test_deploy_stops_if_predeploy_backup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, docker_log = self._environment(
                Path(temporary),
                running_services="db-backup",
                backup_status=42,
            )
            result = subprocess.run(
                ["/bin/sh", str(REMOTE_DEPLOY_SCRIPT)],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 42)
            self.assertNotIn(" pull ", docker_log.read_text(encoding="utf-8"))

    def test_deploy_pulls_only_app_images_and_disables_implicit_pulls(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, docker_log = self._environment(
                Path(temporary),
                running_services="db-backup",
            )
            subprocess.run(
                ["/bin/sh", str(REMOTE_DEPLOY_SCRIPT)],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            commands = docker_log.read_text(encoding="utf-8").splitlines()
            pull_commands = [
                command for command in commands if " pull " in f" {command} "
            ]
            self.assertEqual(
                pull_commands,
                ["compose -f docker-compose.prod.yml pull --quiet backend frontend"],
            )
            up_command = next(command for command in commands if " up -d " in command)
            self.assertIn("--pull never", up_command)


class ProductionManifestTests(unittest.TestCase):
    def test_shell_scripts_parse(self) -> None:
        for script in (BACKUP_SCRIPT, REMOTE_DEPLOY_SCRIPT):
            subprocess.run(["/bin/sh", "-n", str(script)], check=True)

    def test_compose_requires_apipay_secrets_and_has_monitor_healthcheck(
        self,
    ) -> None:
        compose = PROD_COMPOSE.read_text(encoding="utf-8")
        self.assertIn(
            "APIPAY_API_KEY: ${APIPAY_API_KEY:?set APIPAY_API_KEY}",
            compose,
        )
        self.assertIn(
            "APIPAY_WEBHOOK_SECRET: "
            "${APIPAY_WEBHOOK_SECRET:?set APIPAY_WEBHOOK_SECRET}",
            compose,
        )
        self.assertIn(
            'test: ["CMD", "python", "/app/apipay_monitor_healthcheck.py"]',
            compose,
        )

    def test_automatic_deploy_accepts_only_this_repository_main_push(
        self,
    ) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "deploy-production.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "github.event.workflow_run.event == 'push'",
            workflow,
        )
        self.assertIn(
            "github.event.workflow_run.head_branch == 'main'",
            workflow,
        )
        self.assertIn(
            "github.event.workflow_run.head_repository.full_name "
            "== github.repository",
            workflow,
        )

    def test_scale_endpoint_secret_is_forwarded_to_production(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "deploy-production.yml"
        ).read_text(encoding="utf-8")
        deploy_script = REMOTE_DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "TRUCK_SCALE_API_URL: ${{ secrets.TRUCK_SCALE_API_URL }}",
            workflow,
        )
        self.assertIn('test -n "$TRUCK_SCALE_API_URL"', workflow)
        self.assertIn("TRUCK_SCALE_API_URL_B64='$TRUCK_SCALE_API_URL_B64'", workflow)
        self.assertIn('base64 -d)', deploy_script)
        self.assertIn("export TRUCK_SCALE_API_URL", deploy_script)


class SecurityHeaderTests(unittest.TestCase):
    """Заголовки не должны молча отключать то, чем пользуются сотрудники."""

    def _headers(self) -> str:
        return (
            REPO_ROOT / "deploy" / "nginx" / "conf.d" / "snippets"
            / "security-headers.conf"
        ).read_text(encoding="utf-8")

    def test_microphone_is_allowed_for_voice_tasks(self) -> None:
        # Пустой список запрещал запись всему сайту, и браузер отказывал,
        # даже не спросив сотрудника — голосовые задачи было не записать.
        headers = self._headers()
        self.assertIn("microphone=(self)", headers)
        self.assertNotIn("microphone=()", headers)

    def test_camera_and_geolocation_stay_closed(self) -> None:
        headers = self._headers()
        self.assertIn("camera=()", headers)
        self.assertIn("geolocation=()", headers)

    def test_baseline_headers_are_present(self) -> None:
        headers = self._headers()
        for header in (
            "Strict-Transport-Security",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
        ):
            self.assertIn(header, headers)


class ConveyorDeviceIngressTests(unittest.TestCase):
    """The high-rate ESP path stays bounded and separate from operator API."""

    def test_device_sync_has_an_exact_small_body_location(self) -> None:
        nginx = (
            REPO_ROOT / "deploy" / "nginx" / "conf.d" / "asyl-ltd.conf"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "limit_req_zone $binary_remote_addr "
            "zone=asyl_conveyor_device_per_ip:10m rate=100r/s;",
            nginx,
        )
        self.assertIn(
            "location = /api/conveyors/v1/device/sync/ {",
            nginx,
        )
        self.assertIn(
            "location = /api/conveyors/v1/ai/observation/ {",
            nginx,
        )
        self.assertIn(
            "limit_req zone=asyl_conveyor_device_per_ip burst=200 nodelay;",
            nginx,
        )
        self.assertIn("client_max_body_size 16k;", nginx)


if __name__ == "__main__":
    unittest.main()
