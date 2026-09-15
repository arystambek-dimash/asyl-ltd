from __future__ import annotations

import base64
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
GO2RTC_CONFIG = REPO_ROOT / "deploy" / "go2rtc" / "go2rtc.yaml"
CAMERA_HEALTH_GATE = REPO_ROOT / "deploy" / "health" / "wait-for-camera-health.sh"
WEIGHBRIDGE_COMPOSE = REPO_ROOT / "deploy" / "weighbridge" / "compose.yml"
WEIGHBRIDGE_INSTALL = REPO_ROOT / "deploy" / "weighbridge" / "install.sh"
WEIGHBRIDGE_GO2RTC = REPO_ROOT / "deploy" / "weighbridge" / "go2rtc.yaml"

CANDIDATE_BACKEND = (
    "ghcr.io/arystambek-dimash/asyl-ltd-backend@sha256:" + "a" * 64
)
CANDIDATE_FRONTEND = (
    "ghcr.io/arystambek-dimash/asyl-ltd-frontend@sha256:" + "b" * 64
)
PREVIOUS_BACKEND = (
    "ghcr.io/arystambek-dimash/asyl-ltd-backend@sha256:" + "c" * 64
)
PREVIOUS_FRONTEND = (
    "ghcr.io/arystambek-dimash/asyl-ltd-frontend@sha256:" + "d" * 64
)
CANDIDATE_SHA = "a" * 40
PREVIOUS_SHA = "c" * 40


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
        candidate_up_status: int = 0,
        cutover_status: int = 0,
    ) -> tuple[dict[str, str], Path, Path]:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        git_head = root / "git-head"
        git_head.write_text(PREVIOUS_SHA, encoding="utf-8")
        git_log = root / "git.log"
        _write_executable(
            fake_bin / "git",
            """#!/bin/sh
set -eu
printf '%s\\n' "$*" >>"$FAKE_GIT_LOG"
case "$*" in
  "rev-parse HEAD")
    cat "$FAKE_GIT_HEAD_FILE"
    ;;
  "rev-parse origin/main")
    printf '%s\\n' "$FAKE_CANDIDATE_SHA"
    ;;
  "checkout main")
    ;;
  "merge --ff-only "*)
    printf '%s\\n' "$FAKE_CANDIDATE_SHA" >"$FAKE_GIT_HEAD_FILE"
    ;;
  "checkout --detach "*)
    printf '%s\\n' "$3" >"$FAKE_GIT_HEAD_FILE"
    ;;
esac
exit 0
""",
        )
        _write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
        backend_current = root / "backend-current"
        frontend_current = root / "frontend-current"
        backend_current.write_text(PREVIOUS_BACKEND, encoding="utf-8")
        frontend_current.write_text(PREVIOUS_FRONTEND, encoding="utf-8")
        _write_executable(
            fake_bin / "docker",
            """#!/bin/sh
set -eu
printf '%s\\n' "$*" >>"$FAKE_DOCKER_LOG"
printf 'backend=%s frontend=%s release=%s command=%s\\n' \\
  "${BACKEND_IMAGE_REF:-}" "${FRONTEND_IMAGE_REF:-}" \\
  "${APP_RELEASE:-}" "$*" \\
  >>"$FAKE_DOCKER_ENV_LOG"
case "$*" in
  *" ps --services --filter status=running")
    printf '%s\\n' "${FAKE_RUNNING_SERVICES:-}"
    ;;
  "compose -f docker-compose.prod.yml ps -q backend")
    if [ -s "$FAKE_BACKEND_CURRENT_FILE" ]; then printf '%s\\n' backend-container; fi
    ;;
  "compose -f docker-compose.prod.yml ps -q frontend")
    if [ -s "$FAKE_FRONTEND_CURRENT_FILE" ]; then printf '%s\\n' frontend-container; fi
    ;;
  "inspect --format {{.Config.Image}} backend-container")
    cat "$FAKE_BACKEND_CURRENT_FILE"
    ;;
  "inspect --format {{.Config.Image}} frontend-container")
    cat "$FAKE_FRONTEND_CURRENT_FILE"
    ;;
  *" exec -T db-backup /backup/backup.sh")
    exit "${FAKE_BACKUP_STATUS:-0}"
    ;;
  *"run --rm --no-deps --entrypoint python backend manage.py check_camera_cutover")
    exit "${FAKE_CUTOVER_STATUS:-0}"
    ;;
  *"manage.py activate_weighbridge_collector")
    exit "${FAKE_WEIGHBRIDGE_STATUS:-0}"
    ;;
  "image inspect "*)
    case "$*" in
      *"$FAKE_PREVIOUS_BACKEND"|*"$FAKE_PREVIOUS_FRONTEND")
        if [ "${FAKE_PREVIOUS_IMAGES_MISSING:-0}" = "1" ]; then exit 1; fi
        ;;
    esac
    ;;
  *" up -d "*)
    printf '%s' "${BACKEND_IMAGE_REF:-}" >"$FAKE_BACKEND_CURRENT_FILE"
    if [ "${FAKE_PARTIAL_CANDIDATE_UP:-0}" != "1" ] || \\
       [ "${BACKEND_IMAGE_REF:-}" != "$FAKE_CANDIDATE_BACKEND" ]; then
      printf '%s' "${FRONTEND_IMAGE_REF:-}" >"$FAKE_FRONTEND_CURRENT_FILE"
    fi
    if [ "${BACKEND_IMAGE_REF:-}" = "$FAKE_CANDIDATE_BACKEND" ]; then
      exit "${FAKE_CANDIDATE_UP_STATUS:-0}"
    fi
    ;;
esac
exit 0
""",
        )
        app_dir = root / "app"
        cleanup = app_dir / "deploy" / "maintenance" / "cleanup-docker.sh"
        cleanup.parent.mkdir(parents=True)
        _write_executable(
            cleanup,
            "#!/bin/sh\nprintf '%s\\n' cleanup >>\"$FAKE_DOCKER_LOG\"\n",
        )
        docker_log = root / "docker.log"
        docker_env_log = root / "docker-env.log"
        state_file = root / "deploy-release-state"
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
                "APP_DIR": str(app_dir),
                "LOCK_FILE": str(root / "deploy.lock"),
                "RELEASE_STATE_FILE": str(state_file),
                "BACKEND_IMAGE_REF": CANDIDATE_BACKEND,
                "FRONTEND_IMAGE_REF": CANDIDATE_FRONTEND,
                "EXPECTED_SHA": CANDIDATE_SHA,
                "FAKE_DOCKER_LOG": str(docker_log),
                "FAKE_DOCKER_ENV_LOG": str(docker_env_log),
                "FAKE_RUNNING_SERVICES": running_services,
                "FAKE_BACKUP_STATUS": str(backup_status),
                "FAKE_CANDIDATE_UP_STATUS": str(candidate_up_status),
                "FAKE_CUTOVER_STATUS": str(cutover_status),
                "FAKE_CANDIDATE_BACKEND": CANDIDATE_BACKEND,
                "FAKE_PREVIOUS_BACKEND": PREVIOUS_BACKEND,
                "FAKE_PREVIOUS_FRONTEND": PREVIOUS_FRONTEND,
                "FAKE_BACKEND_CURRENT_FILE": str(backend_current),
                "FAKE_FRONTEND_CURRENT_FILE": str(frontend_current),
                "FAKE_GIT_HEAD_FILE": str(git_head),
                "FAKE_GIT_LOG": str(git_log),
                "FAKE_CANDIDATE_SHA": CANDIDATE_SHA,
            }
        )
        return environment, docker_log, state_file

    def _run(
        self,
        environment: dict[str, str],
        action: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = ["/bin/sh", str(REMOTE_DEPLOY_SCRIPT)]
        if action is not None:
            command.append(action)
        return subprocess.run(
            command,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _state(path: Path) -> dict[str, str]:
        return dict(
            line.split("=", maxsplit=1)
            for line in path.read_text(encoding="utf-8").splitlines()
        )

    def test_deploy_refuses_when_backup_service_is_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, docker_log, _ = self._environment(
                Path(temporary),
                running_services="",
            )
            result = self._run(environment)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("without a running db-backup", result.stderr)
            self.assertNotIn(" pull ", docker_log.read_text(encoding="utf-8"))

    def test_deploy_stops_if_predeploy_backup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, docker_log, _ = self._environment(
                Path(temporary),
                running_services="db-backup",
                backup_status=42,
            )
            result = self._run(environment)

            self.assertEqual(result.returncode, 42)
            self.assertNotIn(" pull ", docker_log.read_text(encoding="utf-8"))

    def test_deploy_pulls_only_app_images_and_disables_implicit_pulls(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, docker_log, _ = self._environment(
                Path(temporary),
                running_services="db-backup",
            )
            result = self._run(environment)
            self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_both_scale_urls_may_be_explicitly_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, _, _ = self._environment(
                Path(temporary),
                running_services="db-backup",
            )
            environment["WAGON_SCALE_API_URL_B64"] = ""
            environment["TRUCK_SCALE_API_URL_B64"] = ""

            result = self._run(environment)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_nonempty_scale_url_must_be_absolute_http(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, docker_log, _ = self._environment(
                Path(temporary),
                running_services="db-backup",
            )
            environment["WAGON_SCALE_API_URL_B64"] = base64.b64encode(
                b"file:///etc/passwd"
            ).decode()
            environment["TRUCK_SCALE_API_URL_B64"] = ""

            result = self._run(environment)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "WAGON_SCALE_API_URL must be empty or an absolute HTTP(S) URL",
                result.stderr,
            )
            self.assertFalse(docker_log.exists())

    def test_deploy_records_previous_release_before_candidate_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, docker_log, state_file = self._environment(
                root,
                running_services="db-backup",
            )

            result = self._run(environment)

            self.assertEqual(result.returncode, 0, result.stderr)
            state = self._state(state_file)
            self.assertEqual(state["STATE_STATUS"], "PENDING")
            self.assertEqual(state["PREVIOUS_BACKEND_IMAGE_REF"], PREVIOUS_BACKEND)
            self.assertEqual(state["PREVIOUS_FRONTEND_IMAGE_REF"], PREVIOUS_FRONTEND)
            self.assertEqual(state["PREVIOUS_GIT_SHA"], PREVIOUS_SHA)
            self.assertEqual(state["CANDIDATE_BACKEND_IMAGE_REF"], CANDIDATE_BACKEND)
            self.assertEqual(state["CANDIDATE_FRONTEND_IMAGE_REF"], CANDIDATE_FRONTEND)
            self.assertEqual(state["CANDIDATE_GIT_SHA"], CANDIDATE_SHA)
            self.assertEqual(stat.S_IMODE(state_file.stat().st_mode), 0o600)
            runner = Path(f"{state_file}.runner")
            self.assertTrue(runner.exists())
            self.assertEqual(stat.S_IMODE(runner.stat().st_mode), 0o700)
            commands = docker_log.read_text(encoding="utf-8")
            self.assertLess(commands.index("ps -q backend"), commands.index("up -d"))

            environment_log = Path(
                environment["FAKE_DOCKER_ENV_LOG"]
            ).read_text(encoding="utf-8")
            candidate_start = next(
                line for line in environment_log.splitlines() if " up -d " in line
            )
            self.assertIn(f"release={CANDIDATE_SHA}", candidate_start)

    def test_deploy_quiesces_old_camera_writers_before_cutover_and_candidate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, docker_log, _ = self._environment(
                Path(temporary),
                running_services="db-backup",
            )

            result = self._run(environment)

            self.assertEqual(result.returncode, 0, result.stderr)
            commands = docker_log.read_text(encoding="utf-8")
            stop = (
                "stop -t 180 backend camera-monitor ai-stock-monitor "
                "passage-scale-monitor shipping-transport-monitor"
            )
            cutover = "backend manage.py check_camera_cutover"
            self.assertIn(stop, commands)
            self.assertLess(commands.index(stop), commands.index(cutover))
            self.assertLess(commands.index(cutover), commands.index("up -d"))

    def test_cutover_refusal_resumes_previous_camera_writers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, docker_log, _ = self._environment(
                Path(temporary),
                running_services="db-backup",
                cutover_status=17,
            )

            result = self._run(environment)

            self.assertEqual(result.returncode, 2)
            self.assertIn("Camera contour cutover refused", result.stderr)
            self.assertIn("resuming the previous camera writers", result.stderr)
            commands = docker_log.read_text(encoding="utf-8")
            self.assertIn(
                "start backend camera-monitor ai-stock-monitor "
                "passage-scale-monitor shipping-transport-monitor",
                commands,
            )
            self.assertNotIn(" up -d ", f" {commands} ")

    def test_independent_collector_handoff_precedes_candidate_and_refuses_busy(self):
        for handoff_status in (0, 1):
            with self.subTest(handoff_status=handoff_status), tempfile.TemporaryDirectory() as temporary:
                environment, docker_log, _ = self._environment(
                    Path(temporary), running_services="db-backup"
                )
                installer = Path(environment["APP_DIR"]) / "deploy/weighbridge/install.sh"
                installer.parent.mkdir()
                _write_executable(installer, '#!/bin/sh\nprintf "collector prepare\\n" >> "$FAKE_DOCKER_LOG"\n')
                environment["FAKE_WEIGHBRIDGE_STATUS"] = str(handoff_status)
                result = self._run(environment)
                commands = docker_log.read_text()
                handoff = "manage.py activate_weighbridge_collector"
                self.assertLess(commands.index("collector prepare"), commands.index("stop -t 180"))
                self.assertLess(commands.index("check_camera_cutover"), commands.index(handoff))
                if handoff_status:
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("start backend camera-monitor", commands)
                    self.assertNotIn(" up -d ", commands)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertLess(commands.index(handoff), commands.index(" up -d "))

    def test_same_candidate_retry_preserves_original_rollback_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _, state_file = self._environment(
                root,
                running_services="db-backup",
                backup_status=42,
            )
            first = self._run(environment)
            self.assertEqual(first.returncode, 42)

            Path(environment["FAKE_BACKEND_CURRENT_FILE"]).write_text(
                CANDIDATE_BACKEND,
                encoding="utf-8",
            )
            Path(environment["FAKE_FRONTEND_CURRENT_FILE"]).write_text(
                PREVIOUS_FRONTEND,
                encoding="utf-8",
            )
            Path(environment["FAKE_GIT_HEAD_FILE"]).write_text(
                CANDIDATE_SHA,
                encoding="utf-8",
            )
            environment["FAKE_BACKUP_STATUS"] = "0"

            second = self._run(environment)

            self.assertEqual(second.returncode, 0, second.stderr)
            state = self._state(state_file)
            self.assertEqual(state["PREVIOUS_BACKEND_IMAGE_REF"], PREVIOUS_BACKEND)
            self.assertEqual(state["PREVIOUS_FRONTEND_IMAGE_REF"], PREVIOUS_FRONTEND)
            self.assertEqual(state["PREVIOUS_GIT_SHA"], PREVIOUS_SHA)
            self.assertIn("Reusing rollback state", second.stdout)

    def test_corrupt_pending_state_fails_closed_before_container_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, docker_log, state_file = self._environment(
                root,
                running_services="db-backup",
            )
            state_file.write_text("STATE_VERSION=broken\n", encoding="utf-8")

            result = self._run(environment)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("corrupt pending release state", result.stderr)
            self.assertNotIn(" up -d ", docker_log.read_text(encoding="utf-8"))

    def test_candidate_startup_failure_restores_previous_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _, state_file = self._environment(
                root,
                running_services="db-backup",
                candidate_up_status=42,
            )

            result = self._run(environment)

            self.assertEqual(result.returncode, 42)
            self.assertIn("Automatic local rollback succeeded", result.stderr)
            self.assertEqual(
                Path(environment["FAKE_BACKEND_CURRENT_FILE"]).read_text(
                    encoding="utf-8"
                ),
                PREVIOUS_BACKEND,
            )
            self.assertEqual(
                Path(environment["FAKE_FRONTEND_CURRENT_FILE"]).read_text(
                    encoding="utf-8"
                ),
                PREVIOUS_FRONTEND,
            )
            self.assertEqual(
                Path(environment["FAKE_GIT_HEAD_FILE"]).read_text(encoding="utf-8"),
                PREVIOUS_SHA + "\n",
            )
            self.assertEqual(self._state(state_file)["STATE_STATUS"], "PENDING")
            env_commands = Path(environment["FAKE_DOCKER_ENV_LOG"]).read_text(
                encoding="utf-8"
            )
            self.assertIn(f"backend={CANDIDATE_BACKEND}", env_commands)
            self.assertIn(f"backend={PREVIOUS_BACKEND}", env_commands)
            rollback_start = next(
                line
                for line in env_commands.splitlines()
                if f"backend={PREVIOUS_BACKEND}" in line and " up -d " in line
            )
            self.assertIn(f"release={PREVIOUS_SHA}", rollback_start)
            self.assertIn("--scale ai-stock-monitor=0", rollback_start)
            self.assertIn(
                "ai-stock-monitor is intentionally disabled after rollback",
                result.stderr,
            )

    def test_rollback_pulls_missing_previous_images_by_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, docker_log, _ = self._environment(
                root,
                running_services="db-backup",
                candidate_up_status=42,
            )
            environment["FAKE_PREVIOUS_IMAGES_MISSING"] = "1"
            environment["GHCR_TOKEN"] = "test-token"

            result = self._run(environment)

            self.assertEqual(result.returncode, 42)
            commands = docker_log.read_text(encoding="utf-8")
            self.assertIn(f"pull {PREVIOUS_BACKEND}", commands)
            self.assertIn(f"pull {PREVIOUS_FRONTEND}", commands)

    def test_explicit_rollback_completes_pending_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _, state_file = self._environment(
                root,
                running_services="db-backup",
                candidate_up_status=42,
            )
            failed = self._run(environment)
            self.assertEqual(failed.returncode, 42)

            runner = Path(f"{state_file}.runner")
            result = subprocess.run(
                ["/bin/sh", str(runner), "rollback"],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(state_file.exists())
            self.assertEqual(
                self._state(Path(f"{state_file}.prev"))["STATE_STATUS"],
                "ROLLED_BACK",
            )

    def test_cleanup_runs_only_after_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, docker_log, state_file = self._environment(
                root,
                running_services="db-backup",
            )
            deployed = self._run(environment)
            self.assertEqual(deployed.returncode, 0, deployed.stderr)
            self.assertNotIn("cleanup", docker_log.read_text(encoding="utf-8"))

            runner = Path(f"{state_file}.runner")
            finalized = subprocess.run(
                ["/bin/sh", str(runner), "finalize"],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(finalized.returncode, 0, finalized.stderr)
            self.assertIn("cleanup", docker_log.read_text(encoding="utf-8"))
            self.assertFalse(state_file.exists())
            self.assertEqual(
                self._state(Path(f"{state_file}.prev"))["STATE_STATUS"],
                "FINALIZED",
            )

    def test_finalized_candidate_can_roll_back_from_durable_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _, state_file = self._environment(
                root,
                running_services="db-backup",
            )
            deployed = self._run(environment)
            self.assertEqual(deployed.returncode, 0, deployed.stderr)

            runner = Path(f"{state_file}.runner")
            finalized = subprocess.run(
                ["/bin/sh", str(runner), "finalize"],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(finalized.returncode, 0, finalized.stderr)

            rolled_back = subprocess.run(
                ["/bin/sh", str(runner), "rollback"],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
            self.assertIn("Finalized candidate was unhealthy", rolled_back.stdout)
            self.assertEqual(
                Path(environment["FAKE_BACKEND_CURRENT_FILE"]).read_text(
                    encoding="utf-8"
                ),
                PREVIOUS_BACKEND,
            )
            self.assertEqual(
                Path(environment["FAKE_FRONTEND_CURRENT_FILE"]).read_text(
                    encoding="utf-8"
                ),
                PREVIOUS_FRONTEND,
            )
            self.assertEqual(
                self._state(Path(f"{state_file}.prev"))["STATE_STATUS"],
                "ROLLED_BACK",
            )


class ProductionManifestTests(unittest.TestCase):
    def test_passage_scale_monitor_is_liveness_checked_and_non_public(self) -> None:
        compose = PROD_COMPOSE.read_text(encoding="utf-8")
        monitor = compose.split("\n  passage-scale-monitor:\n", 1)[1].split(
            "\n  celery-payments:\n",
            1,
        )[0]

        self.assertIn('APP_SERVICE: passage-scale-monitor', monitor)
        self.assertIn('entrypoint: []', monitor)
        self.assertIn(
            'command: ["python", "manage.py", "monitor_passage_scale"]',
            monitor,
        )
        self.assertIn("backend:\n        condition: service_healthy", monitor)
        self.assertIn("init: true", monitor)
        self.assertIn("stop_grace_period: 60s", monitor)
        self.assertIn("/app/passage_scale_monitor_healthcheck.py", monitor)
        self.assertIn("restart: unless-stopped", monitor)
        self.assertIn("logging: *default-logging", monitor)
        self.assertNotIn("ports:", monitor)

    def test_camera_monitor_waits_for_migrated_healthy_backend(self) -> None:
        compose = PROD_COMPOSE.read_text(encoding="utf-8")
        monitor = compose.split("\n  camera-monitor:\n", 1)[1].split(
            "\n  ai-stock-monitor:\n",
            1,
        )[0]

        self.assertIn("backend:\n        condition: service_healthy", monitor)

    def test_camera_health_gate_reports_last_event_sync_diagnostic(self) -> None:
        gate = CAMERA_HEALTH_GATE.read_text(encoding="utf-8")

        self.assertIn("check_camera_health --human", gate)
        self.assertIn('last_output="$output"', gate)
        self.assertIn("Последняя диагностика camera-monitor:", gate)
        self.assertIn("printf '%s\\n' \"$last_output\" >&2", gate)

    def test_shell_scripts_parse(self) -> None:
        for script in (BACKUP_SCRIPT, REMOTE_DEPLOY_SCRIPT):
            subprocess.run(["/bin/sh", "-n", str(script)], check=True)

    def test_release_state_is_flushed_before_checkout_mutation(self) -> None:
        deploy_script = REMOTE_DEPLOY_SCRIPT.read_text(encoding="utf-8")
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "deploy-production.yml"
        ).read_text(encoding="utf-8")

        persist_runner = deploy_script.index("persist_release_runner()")
        write_state = deploy_script.index("write_state()")
        prepare_state = deploy_script.index("prepare_release_state\n")
        checkout = deploy_script.index('git checkout "$BRANCH"', prepare_state)
        self.assertIn(
            "STATE_TEMP_FILE=\"\"\n  sync",
            deploy_script[write_state:persist_runner],
        )
        self.assertIn(
            "STATE_TEMP_FILE=\"\"\n  sync",
            deploy_script[persist_runner:prepare_state],
        )
        self.assertLess(prepare_state, checkout)
        self.assertIn(
            r'&& mv -f \"\$incoming\" \"\$runner\" \
               && sync',
            workflow,
        )

    def test_compose_keeps_apipay_env_optional_and_serial_celery_topology(
        self,
    ) -> None:
        # Ключи ApiPay хранятся у отделов; переменные окружения нужны только
        # разовой миграции и не должны блокировать запуск.
        compose = PROD_COMPOSE.read_text(encoding="utf-8")
        self.assertNotIn("APIPAY_API_KEY:?", compose)
        self.assertNotIn("APIPAY_WEBHOOK_SECRET:?", compose)
        self.assertIn("APIPAY_API_KEY: ${APIPAY_API_KEY:-}", compose)
        self.assertIn(
            "APIPAY_WEBHOOK_SECRET: ${APIPAY_WEBHOOK_SECRET:-}", compose
        )
        self.assertIn(
            'test: ["CMD", "python", "/app/apipay_monitor_healthcheck.py"]',
            compose,
        )
        self.assertNotIn("\n  payment-monitor:\n", compose)
        self.assertEqual(compose.count("\n  celery-payments:\n"), 1)
        self.assertEqual(compose.count("\n  celery-beat:\n"), 1)
        self.assertIn('"--queues=payments"', compose)
        self.assertIn('"--concurrency=1"', compose)
        self.assertIn('"--prefetch-multiplier=1"', compose)
        self.assertIn(
            'test: ["CMD", "python", "/app/celery_beat_healthcheck.py"]',
            compose,
        )
        self.assertEqual(
            compose.count(
                "APP_RELEASE: ${APP_RELEASE:-${EXPECTED_SHA:-development}}"
            ),
            3,
        )

    def test_deploy_failure_reports_recent_health_probe_output(self) -> None:
        deploy_script = REMOTE_DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("range .State.Health.Log", deploy_script)
        self.assertIn("if ne .ExitCode 0", deploy_script)
        self.assertIn(
            'logs --no-color --tail=200 "$diag_service"',
            deploy_script,
        )

    def test_frontend_build_wires_sentry_without_exposing_auth_token_as_arg(
        self,
    ) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "deploy-production.yml"
        ).read_text(encoding="utf-8")
        dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "NEXT_PUBLIC_APP_RELEASE=${{ env.RELEASE_SHA }}",
            workflow,
        )
        self.assertIn(
            "NEXT_PUBLIC_SENTRY_DSN=${{ vars.NEXT_PUBLIC_SENTRY_DSN }}",
            workflow,
        )
        self.assertIn(
            "sentry_auth_token=${{ secrets.SENTRY_AUTH_TOKEN }}",
            workflow,
        )
        self.assertIn(
            "--mount=type=secret,id=sentry_auth_token,required=false",
            dockerfile,
        )
        self.assertNotIn("ARG SENTRY_AUTH_TOKEN", dockerfile)
        self.assertNotIn("ENV SENTRY_AUTH_TOKEN", dockerfile)

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

    def test_scale_endpoint_secrets_are_forwarded_to_production(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "deploy-production.yml"
        ).read_text(encoding="utf-8")
        deploy_script = REMOTE_DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "WAGON_SCALE_API_URL: ${{ secrets.WAGON_SCALE_API_URL }}",
            workflow,
        )
        self.assertIn(
            "TRUCK_SCALE_API_URL: ${{ secrets.TRUCK_SCALE_API_URL }}",
            workflow,
        )
        self.assertNotIn(
            'fail_if_empty WAGON_SCALE_API_URL "$WAGON_SCALE_API_URL"',
            workflow,
        )
        self.assertNotIn(
            'fail_if_empty TRUCK_SCALE_API_URL "$TRUCK_SCALE_API_URL"',
            workflow,
        )
        self.assertNotIn('test -n "$WAGON_SCALE_API_URL"', workflow)
        self.assertNotIn('test -n "$TRUCK_SCALE_API_URL"', workflow)
        self.assertIn("IFS= read -r WAGON_SCALE_API_URL_B64", workflow)
        self.assertIn("IFS= read -r TRUCK_SCALE_API_URL_B64", workflow)
        self.assertIn(
            "export GHCR_TOKEN WAGON_SCALE_API_URL_B64 "
            "TRUCK_SCALE_API_URL_B64",
            workflow,
        )
        self.assertNotIn("GHCR_TOKEN='$GHCR_TOKEN'", workflow)
        self.assertNotIn(
            "WAGON_SCALE_API_URL_B64='$WAGON_SCALE_API_URL_B64'",
            workflow,
        )
        self.assertNotIn(
            "TRUCK_SCALE_API_URL_B64='$TRUCK_SCALE_API_URL_B64'",
            workflow,
        )
        self.assertIn('base64 -d)', deploy_script)
        self.assertIn("export WAGON_SCALE_API_URL", deploy_script)
        self.assertIn("export TRUCK_SCALE_API_URL", deploy_script)
        self.assertIn(
            'if [ -n "$WAGON_SCALE_API_URL" ]; then', deploy_script
        )
        self.assertIn(
            'if [ -n "$TRUCK_SCALE_API_URL" ]; then', deploy_script
        )
        self.assertGreaterEqual(
            workflow.count("IFS= read -r WAGON_SCALE_API_URL_B64"),
            3,
        )
        self.assertGreaterEqual(
            workflow.count(
                "export GHCR_TOKEN WAGON_SCALE_API_URL_B64 "
                "TRUCK_SCALE_API_URL_B64"
            ),
            3,
        )

    def test_production_git_fetch_uses_environment_only_github_auth(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "deploy-production.yml"
        ).read_text(encoding="utf-8")
        deploy_script = REMOTE_DEPLOY_SCRIPT.read_text(encoding="utf-8")

        for text in (workflow, deploy_script):
            self.assertIn(
                "GIT_CONFIG_KEY_0=http.https://github.com/.extraheader",
                text,
            )
            self.assertIn("GIT_TERMINAL_PROMPT=0", text)
            self.assertIn("AUTHORIZATION: basic", text)
            self.assertNotIn("https://x-access-token:", text)

        self.assertIn("git_fetch_origin \"$BRANCH\"", deploy_script)
        self.assertNotIn('git fetch origin "$BRANCH"', deploy_script)

    def test_scale_compose_configuration_routes_by_hardware(self) -> None:
        compose = PROD_COMPOSE.read_text(encoding="utf-8")
        deploy_script = REMOTE_DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "WAGON_SCALE_API_URL: ${WAGON_SCALE_API_URL-}",
            compose,
        )
        self.assertIn(
            "TRUCK_SCALE_API_URL: "
            "${TRUCK_SCALE_API_URL-http://desktop-t5p32d3:8000/api/v1/weight}",
            compose,
        )
        self.assertNotIn(
            "WAGON_SCALE_API_URL-${TRUCK_SCALE_API_URL", compose
        )
        self.assertNotIn(
            'TRUCK_SCALE_API_URL="${WAGON_SCALE_API_URL:-}"', deploy_script
        )
        self.assertIn(
            "if ! grep -q 'WAGON_SCALE_API_URL' \"$COMPOSE_FILE\"; then",
            deploy_script,
        )
        self.assertIn('TRUCK_SCALE_API_URL=""', deploy_script)

    def test_failed_public_gate_rolls_back_before_success_only_cleanup(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "deploy-production.yml"
        ).read_text(encoding="utf-8")
        deploy_script = REMOTE_DEPLOY_SCRIPT.read_text(encoding="utf-8")

        deploy = workflow.index("- name: Deploy over SSH")
        camera_gate = workflow.index("./deploy/health/wait-for-camera-health.sh")
        api_gate = workflow.index("- name: Health check")
        frontend_gate = workflow.index("- name: Frontend redirect safety gate")
        rollback = workflow.index("- name: Roll back failed release")
        finalize = workflow.index(
            "- name: Finalize healthy release and clean Docker artifacts"
        )
        self.assertLess(deploy, camera_gate)
        self.assertLess(camera_gate, api_gate)
        self.assertLess(api_gate, frontend_gate)
        self.assertLess(frontend_gate, rollback)
        self.assertLess(rollback, finalize)
        self.assertIn("- name: Validate production deployment configuration", workflow)
        self.assertIn("fail_if_empty PROD_SSH_KEY", workflow)
        self.assertIn("fail_if_empty PROD_SSH_KNOWN_HOSTS", workflow)
        self.assertNotIn("skipping production deploy", workflow)
        self.assertIn("PROD_HOST: ${{ secrets.PROD_HOST }}", workflow)
        self.assertNotIn("PROD_HOST: ${{ secrets.PROD_HOST ||", workflow)
        self.assertIn(
            "PROD_APP_DIR: /home/${{ secrets.PROD_USER || 'ubuntu' }}/asyl-ltd",
            workflow,
        )
        self.assertNotIn("cd /home/ubuntu/asyl-ltd", workflow)
        self.assertIn("candidate_health_epoch=$(run_ssh date +%s)", workflow)
        self.assertIn("candidate_health_epoch=$((candidate_health_epoch + 1))", workflow)
        self.assertIn("CAMERA_HEALTH_REQUIRE_SINCE_EPOCH", workflow)
        self.assertIn("CAMERA_HEALTH_REQUIRE_EVENTS=1", workflow)
        self.assertEqual(workflow.count("APP_DIR='$PROD_APP_DIR'"), 4)
        self.assertIn("if: ${{ failure() }}", workflow)
        self.assertGreaterEqual(
            workflow.count("printf '%s\\n' \"$PROD_SSH_KEY\" > ~/.ssh/asyl_ltd_deploy_key"),
            3,
        )
        self.assertGreaterEqual(
            workflow.count(
                "printf '%s\\n' \"$PROD_SSH_KNOWN_HOSTS\" > ~/.ssh/asyl_ltd_known_hosts"
            ),
            3,
        )
        self.assertNotIn("StrictHostKeyChecking=accept-new", workflow)
        self.assertEqual(workflow.count("StrictHostKeyChecking=yes"), 4)
        self.assertEqual(workflow.count("UserKnownHostsFile="), 4)
        self.assertIn("runner=./.deploy-state/release-state.runner", workflow)
        self.assertNotIn("runner=./deploy/remote-deploy.sh", workflow)
        self.assertIn(r'\"\$runner\" rollback', workflow)
        self.assertIn("if: ${{ success() }}", workflow)
        self.assertIn(r'\"\$runner\" finalize', workflow)
        self.assertEqual(workflow.count("inspect_response / 307"), 3)
        self.assertEqual(workflow.count("inspect_response /login 200"), 3)
        self.assertGreaterEqual(workflow.count("Foreign redirect is forbidden"), 3)
        self.assertEqual(workflow.count("packages: read"), 2)

        self.assertIn(
            r'runner=\"\$state_dir/candidate-$RELEASE_SHA.runner\"',
            workflow,
        )
        self.assertIn(
            "git show '$RELEASE_SHA:deploy/remote-deploy.sh'",
            workflow,
        )
        self.assertNotIn("git checkout main && git pull", workflow)
        state_boundary = deploy_script.index("prepare_release_state\n")
        checkout = deploy_script.index('git checkout "$BRANCH"', state_boundary)
        self.assertLess(state_boundary, checkout)
        self.assertIn('previous_git_sha="$starting_git_sha"', deploy_script)
        self.assertIn("timeout-minutes: 120", workflow)
        self.assertIn("timeout-minutes: 90", workflow)
        self.assertIn("needs.deploy.result != 'success'", workflow)
        self.assertIn(
            "needs.deploy.outputs.public_gates_passed == 'true' "
            "&& 'finalize' || 'rollback'",
            workflow,
        )
        recovery_start = workflow.index("  recovery:")
        recovery = workflow[recovery_start:]
        pre_finalize_gate = recovery.index(
            'if [ "$RECOVERY_ACTION" = "finalize" ]'
        )
        recovery_action = recovery.index("if ! recover;")
        self.assertLess(pre_finalize_gate, recovery_action)
        self.assertIn("if ! verify_public_health; then", recovery)
        self.assertIn("RECOVERY_ACTION=rollback", recovery)
        self.assertGreaterEqual(recovery.count("verify_public_health"), 3)


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


class VehicleRoiStreamAliasTests(unittest.TestCase):
    def test_all_weight_first_camera_slots_have_sub_and_main_browser_aliases(
        self,
    ) -> None:
        config = GO2RTC_CONFIG.read_text(encoding="utf-8")
        for camera_number in range(1, 33):
            camera = f"cam{camera_number}"
            self.assertIn(f"  {camera}:\n", config)
            self.assertIn(
                f"@${{CAMERA_HOST}}:8554/{camera}sub",
                config,
            )
            self.assertIn(f"  {camera}main:\n", config)
            self.assertIn(
                f"@${{CAMERA_HOST}}:8554/{camera}\n",
                config,
            )


class WagonCollectorDeploymentTests(unittest.TestCase):
    def test_wagon_outbox_is_shared_by_collector_monitor_and_backend(self) -> None:
        prod = PROD_COMPOSE.read_text(encoding="utf-8")
        backend = prod.split("\n  backend:\n", 1)[1].split("\n  frontend:\n", 1)[0]
        monitor = prod.split("\n  passage-scale-monitor:\n", 1)[1].split(
            "\n  celery-payments:\n",
            1,
        )[0]
        top_level_volumes = prod.split("\nvolumes:\n", 1)[1].split("\nnetworks:\n", 1)[0]

        self.assertIn(
            "      - weighbridge-wagon-outbox:/var/lib/weighbridge-wagon:ro\n",
            backend,
        )
        self.assertIn(
            "      - weighbridge-wagon-outbox:/var/lib/weighbridge-wagon\n",
            monitor,
        )
        self.assertIn(
            "  weighbridge-wagon-outbox:\n    name: asyl-weighbridge-wagon-outbox\n",
            top_level_volumes,
        )

        weighbridge = WEIGHBRIDGE_COMPOSE.read_text(encoding="utf-8")
        wagon_collector = weighbridge.split("\n  wagon-collector:\n", 1)[1].split(
            "\nvolumes:\n",
            1,
        )[0]

        self.assertIn(
            "command: [python, -m, weighbridge.wagon_collector]", wagon_collector
        )
        self.assertIn(
            "DJANGO_SETTINGS_MODULE: config.weighbridge_settings", wagon_collector
        )
        self.assertIn(
            "WEIGHBRIDGE_OUTBOX_DIR: /var/lib/weighbridge-wagon", wagon_collector
        )
        self.assertIn(
            "WAGON_SCALE_API_URL: ${WAGON_SCALE_API_URL-http://vesyv:8000/api/v1/weight}",
            wagon_collector,
        )
        for name, default in (
            ("WAGON_ARCH_CAMERA", "cam8"),
            ("WAGON_ARCH_AUTOMATION_ENABLED", "0"),
            ("WAGON_ARCH_STILL_SECONDS", "10"),
            ("WAGON_ARCH_STABLE_SECONDS", "2"),
            ("WAGON_ARCH_STABLE_TOLERANCE_KG", "100"),
            ("WAGON_ARCH_EMPTY_MAX_KG", "1000"),
            ("WAGON_ARCH_NEXT_WAGON_RISE_KG", "5000"),
            ("WAGON_ARCH_MOTION_MAX_AGE_SECONDS", "5"),
            ("WAGON_ARCH_OCR_RETRY_SECONDS", "15"),
            ("WAGON_ARCH_OCR_MAX_ATTEMPTS", "4"),
        ):
            # Every tunable the collector reads must be reachable from the
            # server's .env; one left out silently keeps its code default.
            self.assertIn(
                f"{name}: ${{{name}:-{default}}}",
                wagon_collector,
                f"{name} is not passed through to the wagon collector",
            )
        prod_compose = PROD_COMPOSE.read_text(encoding="utf-8")
        for name, default in (
            ("WAGON_ARCH_CAMERA", "cam8"),
            ("WAGON_ARCH_AUTOMATION_ENABLED", "0"),
            # CRM-side tunables: the grace window before an exit weight is
            # applied and the rise that means the next wagon, not the same one.
            ("WAGON_ARCH_EXIT_GRACE_SECONDS", "600"),
            ("WAGON_ARCH_NEXT_WAGON_RISE_KG", "5000"),
        ):
            self.assertIn(
                f"{name}: ${{{name}:-{default}}}",
                prod_compose,
                f"{name} is not reachable from the server's .env for the backend",
            )
        self.assertIn("GO2RTC_API_URL: http://video:1984", wagon_collector)
        self.assertIn(
            "      - wagon-outbox:/var/lib/weighbridge-wagon\n", wagon_collector
        )
        self.assertIn("init: true", wagon_collector)
        self.assertIn("restart: unless-stopped", wagon_collector)
        self.assertIn("stop_grace_period: 30s", wagon_collector)
        self.assertIn("logging: *logging", wagon_collector)
        self.assertIn(
            "test: [CMD, python, -m, weighbridge.healthcheck]", wagon_collector
        )
        self.assertIn(
            "  wagon-outbox:\n    external: true\n    name: asyl-weighbridge-wagon-outbox\n",
            weighbridge,
        )

        go2rtc = WEIGHBRIDGE_GO2RTC.read_text(encoding="utf-8")
        preload_section = go2rtc.split("preload:\n", 1)[1].split("streams:\n", 1)[0]
        streams_section = go2rtc.split("streams:\n", 1)[1]
        self.assertNotIn("cam8main", preload_section)
        self.assertIn("  cam8main:\n", streams_section)
        self.assertIn(
            "rtsp://${CAMERA_USER}:${CAMERA_PASS}@${CAMERA_HOST}:8554/cam8",
            streams_section,
        )

    def test_upgrade_is_deferred_while_a_wagon_stands_under_the_arch(self) -> None:
        install = WEIGHBRIDGE_INSTALL.read_text(encoding="utf-8")

        self.assertIn(
            "--filter label=com.docker.compose.service=wagon-collector",
            install,
        )
        self.assertIn("Outbox('/var/lib/weighbridge-wagon')", install)
        self.assertIn(
            "'Wagon stands under the arch: upgrade deferred'",
            install,
        )
        self.assertIn(
            "assert not heartbeat.get('pending_writes', 0), "
            "'Wagon collector storage writes are pending: upgrade deferred'",
            install,
        )
        # The wagon outbox stays unacknowledged until the CRM importer is
        # enabled, so a pending count must NOT block the upgrade.
        wagon_guard = install.split("assert_wagon_clear_for_upgrade()", 1)[1].split(
            "\nPY\n",
            1,
        )[0]
        self.assertNotIn("counts()['pending']", wagon_guard)
        # The guard only runs when the service is actually installed.
        self.assertIn('if [ -n "$wagon_collector_id" ]; then', install)

    def test_remote_deploy_chowns_the_wagon_outbox_alongside_the_truck_outbox(
        self,
    ) -> None:
        deploy_script = REMOTE_DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "-c 'chown app:app /var/lib/weighbridge /var/lib/weighbridge-wagon'",
            deploy_script,
        )
        # The chown runs through passage-scale-monitor (same as install.sh),
        # which is why that service's compose entry mounts the wagon volume
        # read-write rather than read-only.
        self.assertIn(
            "--user root --entrypoint /bin/sh passage-scale-monitor",
            deploy_script,
        )


if __name__ == "__main__":
    unittest.main()
