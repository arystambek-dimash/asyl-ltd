import json
from datetime import datetime, timezone

from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder

from apps.cameras import health

_STATUS_LABELS = {
    "degraded": "частично доступен",
    "healthy": "работает",
    "initializing": "запускается",
    "outage": "сбой",
    "unavailable": "нет данных",
}
_EVENT_STATUS_LABELS = {
    "bootstrap_pending": "переносит историю отгрузки",
    "catching_up": "догоняет журнал",
    "error": "ошибка",
    "legacy": "старый API без /events",
    "pending": "ожидает проверки",
    "stale": "устарела",
    "synced": "синхронизирована",
    "unsupported": "/events не поддерживается",
}
_EVENT_DETAIL_LABELS = {
    "camera service returned 404 for /events": "AI-сервис вернул 404 для /events",
    "durable /events support is required for this health gate": (
        "для этого deploy требуется журнал /events"
    ),
    "event journal backlog is being imported": "импортируется очередь журнала событий",
    "event journal cursor is stale": "курсор журнала событий устарел",
    "event journal has not been probed": "журнал событий ещё не проверен",
    "event journal has not been synchronized by this release": (
        "текущий релиз ещё не синхронизировал журнал событий"
    ),
    "event journal sync failed": "синхронизация журнала событий завершилась ошибкой",
    "initial event boundary has not been validated": (
        "начальная граница журнала событий ещё не подтверждена"
    ),
    "shipping analytics history bootstrap is pending": (
        "история отгрузки ещё переносится после разделения контуров"
    ),
}


def human_diagnostics(payload: dict) -> str:
    """Return concise operator-facing diagnostics without leaking credentials."""

    status = str(payload.get("status") or "unavailable")
    label = _STATUS_LABELS.get(status, status)
    online = payload.get("online_count")
    expected = payload.get("expected_count")
    available = (
        f"{online}/{expected}"
        if online is not None and expected is not None
        else "нет данных"
    )
    age = payload.get("age_seconds")
    age_text = f"{age} сек." if age is not None else "нет данных"
    lines = [
        (
            f"Диагностика camera-monitor: статус={label}; "
            f"камеры={available}; heartbeat={age_text}"
        )
    ]

    if payload.get("fresh_since_required_start") is False:
        lines.append("Причина: heartbeat от текущего релиза ещё не получен.")
    elif payload.get("stale"):
        lines.append("Причина: heartbeat camera-monitor отсутствует или устарел.")
    elif payload.get("confirming_outage"):
        lines.append("Причина: camera-monitor подтверждает сбой видеотракта.")

    detail = str(payload.get("detail") or "").strip()
    if detail:
        lines.append(f"Ошибка camera-monitor: {detail}")

    event_sync = payload.get("event_sync") or {}
    if event_sync.get("blocking"):
        blocking_rows = [
            row
            for row in event_sync.get("cameras") or []
            if row.get("status") not in {"synced", "legacy"}
        ]
        if not blocking_rows:
            lines.append("Синхронизация событий заблокирована без деталей камеры.")
        for row in blocking_rows:
            event_status = str(row.get("status") or "pending")
            event_label = _EVENT_STATUS_LABELS.get(event_status, event_status)
            event_detail = str(row.get("detail") or "").strip()
            event_detail = _EVENT_DETAIL_LABELS.get(event_detail, event_detail)
            cursor = row.get("last_event_id")
            cursor_text = f"; последнее событие={cursor}" if cursor is not None else ""
            detail_text = f"; {event_detail}" if event_detail else ""
            lines.append(
                f"События {row.get('camera') or 'неизвестной камеры'}: "
                f"{event_label}{cursor_text}{detail_text}"
            )

    session_cutover = payload.get("session_cutover") or {}
    if session_cutover.get("blocking"):
        sessions = session_cutover.get("sessions") or []
        session_labels = ", ".join(
            f"#{row.get('id')} {row.get('camera')} ({row.get('status')})"
            for row in sessions
        )
        lines.append(
            "Переключение контуров ожидает завершения активных отгрузок"
            + (f": {session_labels}" if session_labels else ".")
        )

    return "\n".join(lines)


class Command(BaseCommand):
    help = "Check the durable camera-monitor heartbeat (0 ok, 2 stale, 3 outage)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-age",
            type=int,
            default=health.STALE_SECONDS,
            help="Maximum heartbeat age in seconds",
        )
        parser.add_argument(
            "--require-since-epoch",
            type=float,
            default=None,
            help="Reject a heartbeat recorded before this Unix timestamp",
        )
        parser.add_argument(
            "--fail-on-degraded",
            action="store_true",
            help="Return exit 4 when at least one expected stream is unavailable",
        )
        parser.add_argument(
            "--require-events",
            action="store_true",
            help="Reject desired always-on cameras that return 404 for /events",
        )
        parser.add_argument(
            "--human",
            action="store_true",
            help="Print concise operator-facing diagnostics instead of JSON",
        )

    def handle(self, *args, **options):
        required_since = (
            datetime.fromtimestamp(options["require_since_epoch"], tz=timezone.utc)
            if options["require_since_epoch"] is not None
            else None
        )
        payload = health.state_payload(
            max_age=max(1, options["max_age"]),
            required_since=required_since,
            require_events=options["require_events"],
        )
        if options["human"]:
            self.stdout.write(human_diagnostics(payload))
        else:
            self.stdout.write(json.dumps(payload, cls=DjangoJSONEncoder, sort_keys=True))
        code = health.exit_code(
            payload, fail_on_degraded=options["fail_on_degraded"]
        )
        if code:
            raise SystemExit(code)
