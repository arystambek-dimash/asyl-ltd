#!/usr/bin/env sh
set -eu

APP_DIR="${APP_DIR:-/home/ubuntu/asyl-ltd}"
BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
EXPECTED_SHA="${EXPECTED_SHA:-}"
DEPLOY_ACTION="${1:-deploy}"
RELEASE_STATE_FILE="${RELEASE_STATE_FILE:-$APP_DIR/.deploy-state/release-state}"
RELEASE_STATE_RECEIPT="${RELEASE_STATE_RECEIPT:-${RELEASE_STATE_FILE}.prev}"
RELEASE_RUNNER_FILE="${RELEASE_RUNNER_FILE:-${RELEASE_STATE_FILE}.runner}"
RELEASE_STATE_DIRECTORY="$(dirname "$RELEASE_STATE_FILE")"
STATE_TEMP_FILE=""
OLD_CAMERA_WRITERS_QUIESCED=0
CANDIDATE_START_ATTEMPTED=0

case "$DEPLOY_ACTION" in
  deploy|rollback|finalize|mark-good) ;;
  *)
    echo "Usage: $0 [deploy|rollback|finalize|mark-good]" >&2
    exit 2
    ;;
esac

cleanup_state_temp() {
  cleanup_status=$?
  if [ "$OLD_CAMERA_WRITERS_QUIESCED" -eq 1 ] && \
     [ "$CANDIDATE_START_ATTEMPTED" -eq 0 ]; then
    echo "Candidate did not start; resuming the previous camera writers." >&2
    if ! (
      cd "$APP_DIR" &&
      docker compose -f "$COMPOSE_FILE" start \
        backend camera-monitor ai-stock-monitor
    ); then
      echo "Failed to resume one or more previous camera writer containers." >&2
      cleanup_status=1
    fi
  fi
  if [ -n "$STATE_TEMP_FILE" ]; then
    rm -f "$STATE_TEMP_FILE"
  fi
  exit "$cleanup_status"
}
trap cleanup_state_temp EXIT

file_owner_uid() {
  stat -c '%u' "$1" 2>/dev/null || stat -f '%u' "$1"
}

prepare_secure_state_directory() {
  if [ "$(dirname "$RELEASE_STATE_RECEIPT")" != "$RELEASE_STATE_DIRECTORY" ] || \
     [ "$(dirname "$RELEASE_RUNNER_FILE")" != "$RELEASE_STATE_DIRECTORY" ]; then
    echo "Release state, receipt and runner must share one private directory." >&2
    return 1
  fi
  if [ -L "$RELEASE_STATE_DIRECTORY" ] || \
     { [ -e "$RELEASE_STATE_DIRECTORY" ] && [ ! -d "$RELEASE_STATE_DIRECTORY" ]; }; then
    echo "Release state directory is not a real directory: $RELEASE_STATE_DIRECTORY" >&2
    return 1
  fi
  mkdir -p "$RELEASE_STATE_DIRECTORY"
  chmod 700 "$RELEASE_STATE_DIRECTORY"
  state_owner_uid="$(file_owner_uid "$RELEASE_STATE_DIRECTORY")" || return 1
  if [ "$state_owner_uid" != "$(id -u)" ]; then
    echo "Release state directory is not owned by the deploy user." >&2
    return 1
  fi
}

is_backend_image_ref() {
  printf '%s\n' "$1" \
    | grep -Eq '^ghcr\.io/arystambek-dimash/asyl-ltd-backend@sha256:[0-9a-f]{64}$'
}

is_frontend_image_ref() {
  printf '%s\n' "$1" \
    | grep -Eq '^ghcr\.io/arystambek-dimash/asyl-ltd-frontend@sha256:[0-9a-f]{64}$'
}

is_git_sha() {
  printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{40}$'
}

if ! is_backend_image_ref "${BACKEND_IMAGE_REF:-}"; then
  echo "BACKEND_IMAGE_REF must be the immutable asyl-ltd backend digest." >&2
  exit 1
fi
if ! is_frontend_image_ref "${FRONTEND_IMAGE_REF:-}"; then
  echo "FRONTEND_IMAGE_REF must be the immutable asyl-ltd frontend digest." >&2
  exit 1
fi
if [ -n "$EXPECTED_SHA" ] && ! is_git_sha "$EXPECTED_SHA"; then
  echo "EXPECTED_SHA must be a 40-character Git commit." >&2
  exit 1
fi
if [ "$DEPLOY_ACTION" != "deploy" ] && [ -z "$EXPECTED_SHA" ]; then
  echo "EXPECTED_SHA is required for $DEPLOY_ACTION." >&2
  exit 1
fi

if [ "${WAGON_SCALE_API_URL_B64+x}" = x ]; then
  WAGON_SCALE_API_URL="$(printf '%s' "$WAGON_SCALE_API_URL_B64" | base64 -d)"
  if [ -n "$WAGON_SCALE_API_URL" ]; then
    if ! printf '%s\n' "$WAGON_SCALE_API_URL" \
      | grep -Eq '^https?://[^[:space:]]+/[^[:space:]]*$'; then
      echo "WAGON_SCALE_API_URL must be empty or an absolute HTTP(S) URL." >&2
      exit 1
    fi
  fi
  export WAGON_SCALE_API_URL
fi
if [ "${TRUCK_SCALE_API_URL_B64+x}" = x ]; then
  TRUCK_SCALE_API_URL="$(
    printf '%s' "$TRUCK_SCALE_API_URL_B64" | base64 -d
  )"
  if [ -n "$TRUCK_SCALE_API_URL" ]; then
    if ! printf '%s\n' "$TRUCK_SCALE_API_URL" \
      | grep -Eq '^https?://[^[:space:]]+/[^[:space:]]*$'; then
      echo "TRUCK_SCALE_API_URL must be empty or an absolute HTTP(S) URL." >&2
      exit 1
    fi
  fi
  export TRUCK_SCALE_API_URL
fi

# Deploy, rollback and finalize all serialize on the same lock. In particular,
# an SSH retry must not race a still-running deploy or roll back a newer release.
LOCK_FILE="${LOCK_FILE:-/tmp/asyl-ltd-deploy.lock}"
exec 9>"$LOCK_FILE"
if ! flock -w 900 9; then
  echo "Не дождались завершения другого деплоя за 15 минут — выходим." >&2
  exit 1
fi

cd "$APP_DIR"
prepare_secure_state_directory

starting_git_sha="$(git rev-parse HEAD)"
if ! is_git_sha "$starting_git_sha"; then
  echo "Current checkout does not resolve to a valid Git commit." >&2
  exit 1
fi

if [ "$DEPLOY_ACTION" = "deploy" ]; then
  # This value is captured only after flock is held. A workflow-side or manual
  # deploy cannot change the checkout between capture and durable state.
  previous_git_sha="$starting_git_sha"

  echo "Deploying ${BRANCH} in ${APP_DIR}"
  git fetch origin "$BRANCH"
  candidate_git_sha="$(git rev-parse "origin/$BRANCH")"
  if ! is_git_sha "$candidate_git_sha"; then
    echo "Candidate remote branch does not resolve to a valid Git commit." >&2
    exit 1
  fi
  if [ -n "$EXPECTED_SHA" ] && [ "$candidate_git_sha" != "$EXPECTED_SHA" ]; then
    echo "Refusing to deploy unverified commit $candidate_git_sha; expected $EXPECTED_SHA." >&2
    exit 1
  fi
else
  candidate_git_sha="$EXPECTED_SHA"
fi

# Keep telemetry and application metadata tied to the image/check-out pair.
# EXPECTED_SHA remains the transaction identity even while rollback exports the
# previous release below.
APP_RELEASE="$candidate_git_sha"
export APP_RELEASE

state_line_value() {
  state_path="$1"
  state_line="$2"
  state_key="$3"
  state_text="$(sed -n "${state_line}p" "$state_path")"
  case "$state_text" in
    "$state_key="*) printf '%s\n' "${state_text#*=}" ;;
    *) return 1 ;;
  esac
}

load_state() {
  state_path="$1"
  if [ ! -f "$state_path" ] || [ -L "$state_path" ]; then
    echo "Release state is missing or is not a regular file: $state_path" >&2
    return 1
  fi
  state_lines="$(wc -l <"$state_path" | tr -d '[:space:]')"
  if [ "$state_lines" != "8" ]; then
    echo "Release state has an invalid line count: $state_path" >&2
    return 1
  fi

  STATE_VERSION="$(state_line_value "$state_path" 1 STATE_VERSION)" || return 1
  STATE_STATUS="$(state_line_value "$state_path" 2 STATE_STATUS)" || return 1
  STATE_PREVIOUS_BACKEND_IMAGE_REF="$(state_line_value "$state_path" 3 PREVIOUS_BACKEND_IMAGE_REF)" || return 1
  STATE_PREVIOUS_FRONTEND_IMAGE_REF="$(state_line_value "$state_path" 4 PREVIOUS_FRONTEND_IMAGE_REF)" || return 1
  STATE_PREVIOUS_GIT_SHA="$(state_line_value "$state_path" 5 PREVIOUS_GIT_SHA)" || return 1
  STATE_CANDIDATE_BACKEND_IMAGE_REF="$(state_line_value "$state_path" 6 CANDIDATE_BACKEND_IMAGE_REF)" || return 1
  STATE_CANDIDATE_FRONTEND_IMAGE_REF="$(state_line_value "$state_path" 7 CANDIDATE_FRONTEND_IMAGE_REF)" || return 1
  STATE_CANDIDATE_GIT_SHA="$(state_line_value "$state_path" 8 CANDIDATE_GIT_SHA)" || return 1

  if [ "$STATE_VERSION" != "1" ]; then
    echo "Unsupported release state version in $state_path." >&2
    return 1
  fi
  case "$STATE_STATUS" in
    PENDING|ROLLED_BACK|FINALIZED) ;;
    *)
      echo "Invalid release state status in $state_path." >&2
      return 1
      ;;
  esac

  if [ "$STATE_PREVIOUS_BACKEND_IMAGE_REF" = "NONE" ] || \
     [ "$STATE_PREVIOUS_FRONTEND_IMAGE_REF" = "NONE" ] || \
     [ "$STATE_PREVIOUS_GIT_SHA" = "NONE" ]; then
    if [ "$STATE_PREVIOUS_BACKEND_IMAGE_REF" != "NONE" ] || \
       [ "$STATE_PREVIOUS_FRONTEND_IMAGE_REF" != "NONE" ] || \
       [ "$STATE_PREVIOUS_GIT_SHA" != "NONE" ]; then
      echo "Release state contains a partial initial-deploy sentinel." >&2
      return 1
    fi
  else
    if ! is_backend_image_ref "$STATE_PREVIOUS_BACKEND_IMAGE_REF" || \
       ! is_frontend_image_ref "$STATE_PREVIOUS_FRONTEND_IMAGE_REF" || \
       ! is_git_sha "$STATE_PREVIOUS_GIT_SHA"; then
      echo "Release state contains an invalid previous release." >&2
      return 1
    fi
  fi

  if ! is_backend_image_ref "$STATE_CANDIDATE_BACKEND_IMAGE_REF" || \
     ! is_frontend_image_ref "$STATE_CANDIDATE_FRONTEND_IMAGE_REF" || \
     ! is_git_sha "$STATE_CANDIDATE_GIT_SHA"; then
    echo "Release state contains an invalid candidate release." >&2
    return 1
  fi
}

state_matches_candidate() {
  [ "$STATE_CANDIDATE_BACKEND_IMAGE_REF" = "$BACKEND_IMAGE_REF" ] && \
    [ "$STATE_CANDIDATE_FRONTEND_IMAGE_REF" = "$FRONTEND_IMAGE_REF" ] && \
    [ "$STATE_CANDIDATE_GIT_SHA" = "$candidate_git_sha" ]
}

write_state() {
  state_target="$1"
  state_status="$2"
  state_previous_backend="$3"
  state_previous_frontend="$4"
  state_previous_sha="$5"
  state_candidate_backend="$6"
  state_candidate_frontend="$7"
  state_candidate_sha="$8"

  mkdir -p "$(dirname "$state_target")"
  STATE_TEMP_FILE="$(mktemp "${state_target}.tmp.XXXXXX")"
  (
    umask 077
    printf '%s\n' \
      'STATE_VERSION=1' \
      "STATE_STATUS=$state_status" \
      "PREVIOUS_BACKEND_IMAGE_REF=$state_previous_backend" \
      "PREVIOUS_FRONTEND_IMAGE_REF=$state_previous_frontend" \
      "PREVIOUS_GIT_SHA=$state_previous_sha" \
      "CANDIDATE_BACKEND_IMAGE_REF=$state_candidate_backend" \
      "CANDIDATE_FRONTEND_IMAGE_REF=$state_candidate_frontend" \
      "CANDIDATE_GIT_SHA=$state_candidate_sha" >"$STATE_TEMP_FILE"
  )
  chmod 600 "$STATE_TEMP_FILE"
  mv -f "$STATE_TEMP_FILE" "$state_target"
  # An atomic rename prevents torn readers; the flush makes the file and
  # directory entry survive a sudden host reboot before mutation begins.
  STATE_TEMP_FILE=""
  sync
}

persist_release_runner() {
  mkdir -p "$(dirname "$RELEASE_RUNNER_FILE")"
  STATE_TEMP_FILE="$(mktemp "${RELEASE_RUNNER_FILE}.tmp.XXXXXX")"
  cp "$0" "$STATE_TEMP_FILE"
  chmod 700 "$STATE_TEMP_FILE"
  mv -f "$STATE_TEMP_FILE" "$RELEASE_RUNNER_FILE"
  # This is a separate barrier from write_state: durable state must never
  # become visible before the exact runner needed to recover it is durable.
  STATE_TEMP_FILE=""
  sync
}

current_service_image() {
  service_name="$1"
  container_id="$(docker compose -f "$COMPOSE_FILE" ps -q "$service_name")" || return 1
  if [ -z "$container_id" ]; then
    return 0
  fi
  docker inspect --format '{{.Config.Image}}' "$container_id"
}

prepare_release_state() {
  if [ -e "$RELEASE_STATE_FILE" ]; then
    if ! load_state "$RELEASE_STATE_FILE"; then
      echo "Refusing to overwrite corrupt pending release state." >&2
      return 1
    fi
    if [ "$STATE_STATUS" != "PENDING" ] || ! state_matches_candidate; then
      echo "Another release transaction is pending; refusing to overwrite its rollback state." >&2
      return 1
    fi
    persist_release_runner
    echo "Reusing rollback state for the same candidate release."
    return 0
  fi

  previous_backend_image_ref="$(current_service_image backend)" || return 1
  previous_frontend_image_ref="$(current_service_image frontend)" || return 1

  if [ -z "$previous_backend_image_ref" ] && [ -z "$previous_frontend_image_ref" ]; then
    previous_backend_image_ref=NONE
    previous_frontend_image_ref=NONE
    state_previous_git_sha=NONE
    echo "No running application release exists; this is an initial deploy without rollback."
  elif [ -z "$previous_backend_image_ref" ] || [ -z "$previous_frontend_image_ref" ]; then
    echo "Refusing to deploy from a partial current application release." >&2
    return 1
  else
    if ! is_backend_image_ref "$previous_backend_image_ref" || \
       ! is_frontend_image_ref "$previous_frontend_image_ref"; then
      echo "Current application containers do not use approved immutable image references." >&2
      return 1
    fi
    state_previous_git_sha="$previous_git_sha"
  fi

  # Publish the trusted transaction runner first. If the process is killed
  # between these two atomic replacements, absence of state still proves no
  # checkout/container mutation occurred; state can never point at no runner.
  persist_release_runner
  write_state \
    "$RELEASE_STATE_FILE" \
    PENDING \
    "$previous_backend_image_ref" \
    "$previous_frontend_image_ref" \
    "$state_previous_git_sha" \
    "$BACKEND_IMAGE_REF" \
    "$FRONTEND_IMAGE_REF" \
    "$candidate_git_sha"
  load_state "$RELEASE_STATE_FILE"
  echo "Recorded atomic rollback state in $RELEASE_STATE_FILE."
}

load_pending_candidate_state() {
  if ! load_state "$RELEASE_STATE_FILE"; then
    return 1
  fi
  if [ "$STATE_STATUS" != "PENDING" ]; then
    echo "Release transaction is not pending." >&2
    return 1
  fi
  if ! state_matches_candidate; then
    echo "Release state belongs to a different candidate; refusing stale action." >&2
    return 1
  fi
}

registry_logged_in=0
registry_login() {
  if [ "$registry_logged_in" = "1" ]; then
    return 0
  fi
  if [ -z "${GHCR_TOKEN:-}" ]; then
    echo "Rollback image is missing locally and GHCR_TOKEN is not available." >&2
    return 1
  fi
  printf '%s' "$GHCR_TOKEN" \
    | docker login ghcr.io -u "${GHCR_USER:-github}" --password-stdin || return 1
  registry_logged_in=1
}

ensure_image_available() {
  image_ref="$1"
  if docker image inspect "$image_ref" >/dev/null 2>&1; then
    return 0
  fi
  echo "Previous image is not local; pulling immutable digest $image_ref..."
  registry_login || return 1
  docker pull "$image_ref"
}

running_release_matches() {
  expected_backend="$1"
  expected_frontend="$2"
  expected_sha="$3"
  running_sha="$(git rev-parse HEAD)" || return 1
  running_backend="$(current_service_image backend)" || return 1
  running_frontend="$(current_service_image frontend)" || return 1
  [ "$running_sha" = "$expected_sha" ] && \
    [ "$running_backend" = "$expected_backend" ] && \
    [ "$running_frontend" = "$expected_frontend" ]
}

show_container_failure() {
  echo "Current container state:" >&2
  docker compose -f "$COMPOSE_FILE" ps --all >&2 || true

  # Compose can abort on a transient unhealthy state which has already
  # recovered by the time diagnostics run. Docker retains the latest probe
  # attempts, so report any recent non-zero health result and the matching
  # service logs without dumping every healthy container's output.
  diag_services="$(docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null)" || diag_services=""
  for diag_service in $diag_services; do
    diag_container_id="$(docker compose -f "$COMPOSE_FILE" ps -aq "$diag_service" 2>/dev/null)" || diag_container_id=""
    [ -n "$diag_container_id" ] || continue
    diag_container_state="$(docker inspect --format '{{.State.Status}}' "$diag_container_id" 2>/dev/null)" || diag_container_state="unknown"
    diag_failed_health="$(docker inspect --format '{{if .State.Health}}{{range .State.Health.Log}}{{if ne .ExitCode 0}}{{.End}} exit={{.ExitCode}} {{.Output}}{{println}}{{end}}{{end}}{{end}}' "$diag_container_id" 2>/dev/null)" || diag_failed_health=""
    if [ "$diag_container_state" != "running" ] || [ -n "$diag_failed_health" ]; then
      echo "$diag_service diagnostics (state=$diag_container_state):" >&2
      if [ -n "$diag_failed_health" ]; then
        printf '%s\n' "$diag_failed_health" >&2
      fi
      docker compose -f "$COMPOSE_FILE" logs --no-color --tail=200 "$diag_service" >&2 || true
    fi
  done
}

rollback_release() {
  if [ "$STATE_PREVIOUS_BACKEND_IMAGE_REF" = "NONE" ]; then
    echo "No previous application release exists; automatic rollback is unavailable." >&2
    return 1
  fi

  echo "Rolling back application images and checkout to the previous known-good release..."
  if ! git cat-file -e "${STATE_PREVIOUS_GIT_SHA}^{commit}"; then
    echo "Previous Git commit is unavailable locally: $STATE_PREVIOUS_GIT_SHA" >&2
    return 1
  fi

  # Do not change the checkout until both immutable rollback images are known
  # to be usable. A registry outage must leave the currently running candidate
  # paired with its own bind-mounted files rather than a half-rollback.
  ensure_image_available "$STATE_PREVIOUS_BACKEND_IMAGE_REF" || return 1
  ensure_image_available "$STATE_PREVIOUS_FRONTEND_IMAGE_REF" || return 1
  git checkout --detach "$STATE_PREVIOUS_GIT_SHA" || return 1

  # A pre-split release used TRUCK_SCALE_API_URL for every Grain weighing.
  # The configured endpoint now belongs only to export trucks, so such an old
  # rollback must disable physical capture instead of exposing it to intake.
  if ! grep -q 'WAGON_SCALE_API_URL' "$COMPOSE_FILE"; then
    TRUCK_SCALE_API_URL=""
    export TRUCK_SCALE_API_URL
  fi

  BACKEND_IMAGE_REF="$STATE_PREVIOUS_BACKEND_IMAGE_REF"
  FRONTEND_IMAGE_REF="$STATE_PREVIOUS_FRONTEND_IMAGE_REF"
  APP_RELEASE="$STATE_PREVIOUS_GIT_SHA"
  export BACKEND_IMAGE_REF FRONTEND_IMAGE_REF APP_RELEASE

  docker compose -f "$COMPOSE_FILE" config --quiet || return 1
  echo "WARNING: ai-stock-monitor is intentionally disabled after rollback; " \
       "deploy an event-aware release to restore automatic stock posting." >&2
  if ! docker compose -f "$COMPOSE_FILE" up -d --remove-orphans \
    --scale ai-stock-monitor=0 --pull never --wait --wait-timeout 180; then
    show_container_failure
    return 1
  fi

  # These files are bind-mounted from Git. Reapply the previous checkout so an
  # application rollback also restores the matching go2rtc/nginx configuration.
  docker compose -f "$COMPOSE_FILE" restart go2rtc || return 1
  docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -t || return 1
  docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -s reload || return 1

  if ! running_release_matches \
    "$STATE_PREVIOUS_BACKEND_IMAGE_REF" \
    "$STATE_PREVIOUS_FRONTEND_IMAGE_REF" \
    "$STATE_PREVIOUS_GIT_SHA"; then
    echo "Rollback completed commands but the running release does not match rollback state." >&2
    return 1
  fi
  echo "Rollback restored the previous release."
}

complete_transaction() {
  final_status="$1"
  write_state \
    "$RELEASE_STATE_RECEIPT" \
    "$final_status" \
    "$STATE_PREVIOUS_BACKEND_IMAGE_REF" \
    "$STATE_PREVIOUS_FRONTEND_IMAGE_REF" \
    "$STATE_PREVIOUS_GIT_SHA" \
    "$STATE_CANDIDATE_BACKEND_IMAGE_REF" \
    "$STATE_CANDIDATE_FRONTEND_IMAGE_REF" \
    "$STATE_CANDIDATE_GIT_SHA"
  rm -f "$RELEASE_STATE_FILE"
}

load_completed_transaction() {
  expected_status="$1"
  if ! load_state "$RELEASE_STATE_RECEIPT"; then
    return 1
  fi
  if [ "$STATE_STATUS" != "$expected_status" ] || ! state_matches_candidate; then
    echo "Completed release receipt does not match the requested action." >&2
    return 1
  fi
}

candidate_was_not_started() {
  running_backend="$(current_service_image backend)" || return 1
  running_frontend="$(current_service_image frontend)" || return 1
  [ "$starting_git_sha" != "$candidate_git_sha" ] && \
    [ "$running_backend" != "$BACKEND_IMAGE_REF" ] && \
    [ "$running_frontend" != "$FRONTEND_IMAGE_REF" ]
}

if [ "$DEPLOY_ACTION" = "rollback" ]; then
  if [ -e "$RELEASE_STATE_FILE" ]; then
    load_pending_candidate_state
    rollback_release
    complete_transaction ROLLED_BACK
  elif [ -e "$RELEASE_STATE_RECEIPT" ]; then
    # An SSH connection can drop after rollback succeeded but before Actions
    # received the exit status. The receipt makes that retry safe and explicit.
    load_state "$RELEASE_STATE_RECEIPT"
    if [ "$STATE_STATUS" = "ROLLED_BACK" ] && state_matches_candidate; then
      if ! running_release_matches \
        "$STATE_PREVIOUS_BACKEND_IMAGE_REF" \
        "$STATE_PREVIOUS_FRONTEND_IMAGE_REF" \
        "$STATE_PREVIOUS_GIT_SHA"; then
        echo "Rollback receipt exists, but the previous release is not running." >&2
        exit 1
      fi
      echo "Rollback was already completed for this candidate."
    elif [ "$STATE_STATUS" = "FINALIZED" ] && state_matches_candidate; then
      # Recovery revalidates public health immediately before finalization. If
      # the candidate degrades after a main-job timeout, the finalized receipt
      # still carries the exact previous release needed for a safe rollback.
      rollback_release
      complete_transaction ROLLED_BACK
      echo "Finalized candidate was unhealthy and has been rolled back."
    elif candidate_was_not_started; then
      echo "Candidate did not cross the durable state boundary; nothing to roll back."
    else
      echo "Completed receipt belongs to another release while this candidate is present." >&2
      exit 1
    fi
  elif candidate_was_not_started; then
    echo "Candidate did not cross the durable state boundary; nothing to roll back."
  else
    echo "Rollback state is missing while candidate files or images are present." >&2
    exit 1
  fi
  exit 0
fi

if [ "$DEPLOY_ACTION" = "finalize" ] || [ "$DEPLOY_ACTION" = "mark-good" ]; then
  if [ -e "$RELEASE_STATE_FILE" ]; then
    load_pending_candidate_state
    if ! running_release_matches \
      "$STATE_CANDIDATE_BACKEND_IMAGE_REF" \
      "$STATE_CANDIDATE_FRONTEND_IMAGE_REF" \
      "$STATE_CANDIDATE_GIT_SHA"; then
      echo "Refusing to mark good: the candidate release is not fully running." >&2
      exit 1
    fi
    complete_transaction FINALIZED
  else
    load_completed_transaction FINALIZED
    if ! running_release_matches \
      "$STATE_CANDIDATE_BACKEND_IMAGE_REF" \
      "$STATE_CANDIDATE_FRONTEND_IMAGE_REF" \
      "$STATE_CANDIDATE_GIT_SHA"; then
      echo "Finalize receipt exists, but the candidate release is not running." >&2
      exit 1
    fi
    echo "Release was already finalized for this candidate."
  fi

  # Cleanup is intentionally deferred until both public health gates pass. The
  # prior images must remain local throughout the automatic rollback window.
  echo "Cleaning safe Docker artifacts after release finalization..."
  ./deploy/maintenance/cleanup-docker.sh
  exit 0
fi

if ! docker compose -f "$COMPOSE_FILE" ps --services --filter status=running \
  | grep -qx db-backup; then
  echo "Refusing to deploy without a running db-backup service." >&2
  echo "For a first install, follow the bootstrap steps in deploy/backup/README.md." >&2
  exit 1
fi

# Record the running release before any database migration or container
# recreation. A retry of the same candidate reuses this state, even if its first
# attempt partially replaced containers or restored the previous checkout.
prepare_release_state

# State is durable before the bind-mounted nginx/go2rtc checkout changes. A
# cancelled job can therefore always restore the files paired with the old
# image digests instead of leaving an unrecorded candidate checkout behind.
echo "Checking out candidate release $candidate_git_sha..."
git checkout "$BRANCH"
git merge --ff-only "$candidate_git_sha"
checked_out_git_sha="$(git rev-parse HEAD)"
if [ "$checked_out_git_sha" != "$candidate_git_sha" ]; then
  echo "Candidate checkout changed after verification." >&2
  exit 1
fi

echo "Writing and validating pre-deploy database/media backup..."
docker compose -f "$COMPOSE_FILE" exec -T db-backup /backup/backup.sh
docker compose -f "$COMPOSE_FILE" exec -T db-backup \
  sh -c 'sha256sum -c /backups/asyl-latest.dump.sha256 && sha256sum -c /backups/media-latest.tar.gz.sha256'

echo "Validating compose config..."
# `config` expands all environment values, including camera/alert credentials.
# Quiet validation keeps those secrets out of a world-readable /tmp file.
docker compose -f "$COMPOSE_FILE" config --quiet

if [ -n "${GHCR_TOKEN:-}" ]; then
  echo "Logging in to ghcr.io..."
  printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "${GHCR_USER:-github}" --password-stdin
fi

echo "Pulling immutable application images..."
# Infrastructure images are upgraded only during an explicit maintenance
# operation. An application release must not silently replace Postgres,
# Redis, nginx, WireGuard, certbot, or go2rtc.
docker compose -f "$COMPOSE_FILE" pull --quiet backend frontend

# Releases before the non-root backend transition created these persistent
# volumes as root. Image-level chown cannot change an already-mounted volume,
# so repair it in a disposable privileged container before Django starts.
echo "Preparing backend volume permissions..."
docker compose -f "$COMPOSE_FILE" run --rm --no-deps \
  --user root \
  --entrypoint /bin/sh \
  backend -c 'chown -R app:app /app/media /app/staticfiles'

# Migration 0028 introduces permanent analytics roles. The previous backend,
# camera importer and stock poster do not understand those fences. Stop every
# old camera writer before the candidate can migrate, then check sessions from
# the candidate image while HTTP is unavailable. This closes the TOCTOU gap:
# no new loading can start after the zero-open-session check, and no old worker
# can post or import across the schema/policy cutover.
echo "Quiescing previous camera writers before role migration..."
OLD_CAMERA_WRITERS_QUIESCED=1
if ! docker compose -f "$COMPOSE_FILE" stop -t 60 \
  backend camera-monitor ai-stock-monitor; then
  echo "Failed to quiesce previous camera writers." >&2
  exit 1
fi
if ! docker compose -f "$COMPOSE_FILE" run --rm --no-deps \
  --entrypoint python \
  backend manage.py check_camera_cutover; then
  echo "Camera contour cutover refused; the previous release will resume." >&2
  exit 2
fi

candidate_status=0
echo "Starting candidate containers..."
CANDIDATE_START_ATTEMPTED=1
if docker compose -f "$COMPOSE_FILE" up -d --remove-orphans \
  --pull never --wait --wait-timeout 180; then
  :
else
  candidate_status=$?
fi
if [ "$candidate_status" -eq 0 ]; then
  OLD_CAMERA_WRITERS_QUIESCED=0
fi

if [ "$candidate_status" -eq 0 ]; then
  echo "Restarting go2rtc to pick up bind-mounted config..."
  if docker compose -f "$COMPOSE_FILE" restart go2rtc; then
    :
  else
    candidate_status=$?
  fi
fi

if [ "$candidate_status" -eq 0 ]; then
  echo "Validating and reloading nginx config (bind-mounted, not picked up by compose)..."
  if docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -t && \
     docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -s reload; then
    :
  else
    candidate_status=$?
  fi
fi

if [ "$candidate_status" -ne 0 ]; then
  echo "Candidate startup or local health validation failed (exit $candidate_status)." >&2
  show_container_failure
  if rollback_release; then
    echo "Automatic local rollback succeeded; leaving the candidate marked failed." >&2
  else
    rollback_status=$?
    echo "Automatic local rollback also failed (exit $rollback_status)." >&2
  fi
  exit "$candidate_status"
fi

echo "Candidate containers are locally healthy; awaiting public health gates:"
docker compose -f "$COMPOSE_FILE" ps
