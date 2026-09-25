# shellcheck shell=bash
# Общая SSH- и health-обвязка GitHub Actions для продакшн-сервера.
# Подключается через `. deploy/ci/prod-lib.sh` в deploy-production.yml
# (jobs deploy и recovery) и в ручных activate/verify-weighbridge.yml.
# Ожидает в окружении PROD_HOST, PROD_PORT, PROD_USER, PROD_SSH_KEY,
# PROD_SSH_KNOWN_HOSTS; для публичных проверок — SITE_URL.

PROD_SSH_KEY_FILE="$HOME/.ssh/asyl_ltd_deploy_key"
PROD_KNOWN_HOSTS_FILE="$HOME/.ssh/asyl_ltd_known_hosts"

fail_if_empty() {
  if [ -z "$2" ]; then
    echo "::error::$1 is required for a production deployment."
    return 1
  fi
}

# Секреты подставляются в удалённую команду, поэтому допускаем только
# безопасные символы.
validate_prod_target() {
  fail_if_empty PROD_HOST "$PROD_HOST" || return 1
  fail_if_empty PROD_PORT "$PROD_PORT" || return 1
  fail_if_empty PROD_USER "$PROD_USER" || return 1
  fail_if_empty PROD_SSH_KEY "$PROD_SSH_KEY" || return 1
  fail_if_empty PROD_SSH_KNOWN_HOSTS "$PROD_SSH_KNOWN_HOSTS" || return 1

  case "$PROD_HOST" in
    *[!A-Za-z0-9.:-]*) echo "::error::PROD_HOST contains unsupported characters."; return 1 ;;
  esac
  case "$PROD_PORT" in
    *[!0-9]*) echo "::error::PROD_PORT must be numeric."; return 1 ;;
  esac
  case "$PROD_USER" in
    *[!A-Za-z0-9_.-]*) echo "::error::PROD_USER contains unsupported characters."; return 1 ;;
  esac
}

# Файлы остаются до конца job: следующие шаги того же job вызывают run_ssh
# без повторной записи ключа.
setup_ssh_key() {
  install -m 700 -d "$HOME/.ssh"
  (
    umask 077
    printf '%s\n' "$PROD_SSH_KEY" >"$PROD_SSH_KEY_FILE"
    printf '%s\n' "$PROD_SSH_KNOWN_HOSTS" >"$PROD_KNOWN_HOSTS_FILE"
  )
  chmod 600 "$PROD_SSH_KEY_FILE" "$PROD_KNOWN_HOSTS_FILE"
}

run_ssh() {
  # ServerAlive 15с × 6 — мёртвое соединение обнаруживается за ~1.5
  # минуты, а не висит десятки минут до job-таймаута.
  ssh \
    -i "$PROD_SSH_KEY_FILE" \
    -p "$PROD_PORT" \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$PROD_KNOWN_HOSTS_FILE" \
    -o ConnectTimeout=10 \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=6 \
    "$PROD_USER@$PROD_HOST" "$@"
}

# Хостинг может перезагрузить сервер в любой момент. Тридцать пауз —
# 5 минут; с worst-case ConnectTimeout весь probe-бюджет около 10.
# Ошибку ssh не глотаем: показываем ключ/доступ vs недоступный хост.
wait_for_ssh() {
  for i in $(seq 1 30); do
    if run_ssh true 2>/tmp/ssh-probe-err.log; then
      echo "SSH доступен (попытка $i)."
      return 0
    fi
    echo "SSH недоступен, ждём 10с (попытка $i/30)…"
    sleep 10
  done
  echo "::error::Сервер недоступен по SSH. Последняя ошибка ssh:"
  cat /tmp/ssh-probe-err.log || true
  return 1
}

# Необязательные значения (URL весов, ключи) уходят на сервер через stdin
# ssh в base64, чтобы пустое значение и спецсимволы дошли как есть.
b64() {
  printf '%s' "$1" | base64 | tr -d '\n'
}

# Бэкенд стартует не мгновенно (миграции, gunicorn) — ждём до 3 минут.
wait_for_public_api() {
  api_url="${SITE_URL}/api/auth/me/"
  for i in $(seq 1 36); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$api_url" || true)
    if [ "$code" = "401" ] || [ "$code" = "200" ]; then
      echo "API отвечает (HTTP $code)."
      return 0
    fi
    echo "API ещё не готов (HTTP $code), попытка $i/36…"
    sleep 5
  done
  echo "::error::API не поднялся (последний код: $code)."
  return 1
}

inspect_response() {
  path="$1"
  expected_code="$2"
  headers=$(mktemp)
  trap 'rm -f "$headers"' RETURN

  code=$(curl -sS --max-time 15 --max-redirs 0 \
    -D "$headers" -o /dev/null -w '%{http_code}' \
    "${SITE_URL}${path}")
  location=$(awk 'tolower($1)=="location:" {sub(/^[^:]*:[[:space:]]*/, ""); sub(/\r$/, ""); print; exit}' "$headers")
  refresh=$(awk 'tolower($1)=="refresh:" {sub(/^[^:]*:[[:space:]]*/, ""); sub(/\r$/, ""); print; exit}' "$headers")

  if [ "$code" != "$expected_code" ]; then
    echo "::error::${path} returned HTTP ${code}; expected ${expected_code}."
    return 1
  fi
  if [ -n "$refresh" ]; then
    echo "::error::${path} returned a forbidden Refresh header."
    return 1
  fi
  case "$location" in
    ""|/*)
      case "$location" in
        //*) echo "::error::Protocol-relative redirect is forbidden: ${location}"; return 1 ;;
      esac
      ;;
    "${SITE_URL}"|"${SITE_URL}"/*|https://www.asyl-ltd.kz|https://www.asyl-ltd.kz/*) ;;
    *) echo "::error::Foreign redirect is forbidden: ${location}"; return 1 ;;
  esac

  if [ "$path" = "/" ]; then
    case "$location" in
      /login|/login\?*|"${SITE_URL}/login"|"${SITE_URL}/login?"*|https://www.asyl-ltd.kz/login|https://www.asyl-ltd.kz/login\?*) ;;
      *) echo "::error::Root did not redirect to the expected login page: ${location:-<missing>}"; return 1 ;;
    esac
  fi
}

# Корень отдаёт редирект на /login, а /login — саму страницу, без чужих
# редиректов и Refresh.
inspect_login_flow() {
  inspect_response / 307 || return 1
  inspect_response /login 200 || return 1
}

verify_public_health() {
  wait_for_public_api || return 1
  inspect_login_flow
}
