"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import type { ShippingAutoFinish } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

const STALE_AFTER_MS = 15_000;

/** This countdown is informational; only the server can complete a loading session. */
export function ShippingAutoFinishDetails({
  value,
  historical = false,
  stale = false,
}: {
  value?: ShippingAutoFinish | null;
  historical?: boolean;
  stale?: boolean;
}) {
  const revision = `${value?.state}:${value?.observed_at}:${value?.remaining_seconds}`;
  const [received, setReceived] = useState(() => ({ revision, at: Date.now() }));
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setReceived({ revision, at: Date.now() });
    setNow(Date.now());
  }, [revision]);
  useEffect(() => {
    if (historical || value?.state !== "waiting") return;
    const timer = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, [historical, value?.state]);

  if (!value || (value.state === "idle" && !value.detail)) return null;
  // Anchor elapsed time to receipt of a new server observation, not the PC's
  // wall clock: operator and server clocks can differ.
  const elapsedMs = received.revision === revision ? Math.max(0, now - received.at) : 0;
  const validCountdown =
    value.remaining_seconds != null &&
    Number.isFinite(value.remaining_seconds) &&
    value.remaining_seconds >= 0 &&
    value.remaining_seconds <= 40 &&
    !!value.observed_at;
  const unavailable =
    !historical && (stale || (value.state === "waiting" && (!validCountdown || elapsedMs >= STALE_AFTER_MS)));
  const remaining = Math.max(0, Math.ceil((value.remaining_seconds ?? 0) - (historical ? 0 : elapsedMs / 1_000)));
  const label = unavailable
    ? "Автозавершение: нет свежих данных"
    : value.state === "waiting"
      ? historical
        ? `До автозавершения оставалось ${remaining} с`
        : remaining > 0
          ? `Автозавершение через ${remaining} с`
          : "Проверяем завершение погрузки"
      : value.state === "blocked"
        ? "Автозавершение приостановлено"
        : value.state === "finishing"
          ? "Завершаем погрузку автоматически"
          : value.state === "completed"
            ? "Погрузка завершена автоматически"
            : "Автозавершение ожидает освобождения зоны";
  const detail = unavailable ? "Отсчёт появится после получения свежих данных от камер." : value.detail;

  return (
    <div className="space-y-2 rounded-md border p-3" role="group" aria-label="Автозавершение погрузки">
      <Badge
        tone={
          unavailable || value.state === "blocked"
            ? "warning"
            : value.state === "completed"
              ? "success"
              : value.state === "waiting" || value.state === "finishing"
                ? "primary"
                : "muted"
        }
      >
        {label}
      </Badge>
      {detail && <p className="text-xs text-[var(--muted-foreground)]">{detail}</p>}
      {historical && value.observed_at && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Зафиксировано: <time dateTime={value.observed_at}>{formatDateTime(value.observed_at)}</time>
        </p>
      )}
    </div>
  );
}
