"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { apiFileUrl, formatKg, grainTripHref } from "@/lib/grain";
import type { WagonArchStop } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { formatDateTime } from "@/lib/utils";
import { archStopReasonLabel } from "@/lib/weighing-evidence";
import { useAuth } from "@/store/auth";

type StopsPage = { results: WagonArchStop[]; next_cursor: number | null };

const STATUS: Record<
  WagonArchStop["status"],
  { label: string; tone: "muted" | "success" | "warning" | "destructive" }
> = {
  open: { label: "Идёт", tone: "success" },
  closed: { label: "Завершена", tone: "muted" },
  attention: { label: "Нужна проверка", tone: "destructive" },
  superseded: { label: "Переставлен", tone: "muted" },
};

function statusView(status: WagonArchStop["status"]) {
  // Неизвестный статус (например, из более новой версии бэкенда) — нейтральный
  // серый бейдж с сырым значением, а не зелёное «Идёт» по умолчанию (m5).
  return STATUS[status] ?? { label: status, tone: "muted" as const };
}

function StopRow({
  row,
  canDismiss,
  onDismissed,
}: {
  row: WagonArchStop;
  canDismiss: boolean;
  onDismissed: (updated: WagonArchStop) => void;
}) {
  const [dismissing, setDismissing] = useState(false);
  const [dismissError, setDismissError] = useState("");
  const status = statusView(row.status);
  const photo = apiFileUrl(row.photo_url);
  const waiting = row.status === "open" && row.blocked_reason ? "Ждёт" : null;
  // Заметка оператора — по форме, а не по конкретному тексту (m3): стоп закрыт,
  // причина блокировки пуста, но деталь есть — значит это deliberate-запись
  // бэкенда (dismiss/ручной ввод), а не предупреждение об ошибке импорта.
  const operatorNote = row.status === "closed" && !row.blocked_reason && row.blocked_detail ? row.blocked_detail : "";
  const showWarningReason = Boolean(row.blocked_reason) && !operatorNote;
  const showDismiss =
    canDismiss && (row.status === "attention" || (row.status === "open" && Boolean(row.blocked_reason)));

  async function dismiss() {
    setDismissing(true);
    setDismissError("");
    try {
      const res = await api.post<WagonArchStop>(`/grain/wagon-arch/stops/${row.id}/dismiss/`);
      onDismissed(res.data);
    } catch (e) {
      setDismissError(apiError(e));
    } finally {
      setDismissing(false);
    }
  }

  return (
    <li className="flex flex-wrap items-start gap-4 px-4 py-3">
      {photo ? (
        <a href={photo} target="_blank" rel="noreferrer">
          {/* eslint-disable-next-line @next/next/no-img-element -- private signed evidence URL */}
          <img
            src={photo}
            alt={`Стоянка ${row.id}`}
            loading="lazy"
            className="h-14 w-24 shrink-0 rounded object-cover"
          />
        </a>
      ) : (
        <span className="flex h-14 w-24 shrink-0 items-center justify-center rounded bg-[var(--muted)] text-xs text-[var(--muted-foreground)]">
          нет кадра
        </span>
      )}
      <div className="min-w-40 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{row.number ? `Вагон ${row.number}` : "Номер не распознан"}</span>
          <Badge tone={waiting ? "warning" : status.tone}>{waiting ?? status.label}</Badge>
          <span className="text-xs text-[var(--muted-foreground)]">{formatDateTime(row.arrived_at)}</span>
        </div>
        <p className="mt-1 text-sm tabular-nums">
          {formatKg(row.full_weight_kg)}
          {row.exit_weight_kg != null && ` → ${formatKg(row.exit_weight_kg)}`}
          {row.continues == null && row.net_kg != null && row.net_kg > 0 && ` · нетто ${formatKg(row.net_kg)}`}
          {row.continues != null && (
            <span className="ml-1 text-[var(--muted-foreground)]" data-testid="continues-note">
              · продолжение стоянки
            </span>
          )}
        </p>
        {showWarningReason && (
          <p className="mt-1 text-sm text-[var(--warning)]">
            {archStopReasonLabel(row.blocked_reason, row.blocked_detail)}
          </p>
        )}
        {operatorNote && <p className="mt-1 text-sm text-[var(--muted-foreground)]">{operatorNote}</p>}
        {dismissError && (
          <p role="alert" className="mt-1 text-sm text-[var(--destructive)]">
            {dismissError}
          </p>
        )}
      </div>
      <div className="flex basis-full flex-col items-end gap-2 sm:basis-auto sm:shrink-0">
        {row.wagon_id != null && (
          <Link
            href={grainTripHref({ id: row.wagon_id, direction: "intake" })}
            className="text-sm underline"
            aria-label={`Открыть рейс вагона ${row.number || "без номера"}`}
          >
            Открыть рейс
          </Link>
        )}
        {showDismiss && (
          <Button variant="outline" size="sm" disabled={dismissing} onClick={() => void dismiss()}>
            {dismissing ? "Закрываем…" : "Закрыть стоянку"}
          </Button>
        )}
      </div>
    </li>
  );
}

export function WagonArchStops() {
  const [before, setBefore] = useState<number | null>(null);
  const canDismiss = useAuth((state) => can(state.me, "grain.edit"));
  const { data, loading, error, reload, setData } = useApi<StopsPage>(
    `/grain/wagon-arch/stops/${before ? `?before=${before}` : ""}`,
  );
  useVisiblePolling(reload, 5000, before === null);

  // Всегда самые свежие данные для replaceRow (m2): dismiss — async-запрос, и
  // пока он летит, может прилететь poll и обновить data. Если replaceRow
  // соберёт замену из data, захваченного при рендере клика (замыкание в
  // StopRow.dismiss), он перезапишет строки, пришедшие после клика, устаревшим
  // снимком. Ref всегда указывает на последний data — обновляется на каждом
  // рендере, а не только через эффект после коммита.
  const dataRef = useRef(data);
  dataRef.current = data;

  function replaceRow(updated: WagonArchStop) {
    const latest = dataRef.current;
    setData(
      latest ? { ...latest, results: latest.results.map((row) => (row.id === updated.id ? updated : row)) } : latest,
    );
  }

  return (
    <section id="wagon-arch-stops" role="tabpanel" aria-labelledby="wagon-arch-stops-tab" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Стоянки под аркой</h2>
          <p className="text-sm text-[var(--muted-foreground)]">
            Каждая стоянка вагона: полный вес, пустой вес и рейс.
          </p>
        </div>
        <Button variant="ghost" size="sm" disabled={loading} onClick={() => void reload()}>
          <RefreshCw className="size-4" /> Обновить
        </Button>
      </div>
      {error && <ErrorAlert message={error} onRetry={() => void reload()} />}
      {loading && !data ? (
        <p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p>
      ) : data?.results.length === 0 ? (
        <p className="rounded-lg bg-[var(--muted)]/35 px-4 py-6 text-center text-sm text-[var(--muted-foreground)]">
          Стоянок под аркой пока нет
        </p>
      ) : (
        <ul className="divide-y divide-[var(--border)] rounded-lg border">
          {(data?.results ?? []).map((row) => (
            <StopRow key={row.id} row={row} canDismiss={canDismiss} onDismissed={replaceRow} />
          ))}
        </ul>
      )}
      <div className="flex justify-between">
        <Button variant="outline" size="sm" disabled={before === null} onClick={() => setBefore(null)}>
          Последние
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!data?.next_cursor}
          onClick={() => setBefore(data?.next_cursor ?? null)}
        >
          Более ранние
        </Button>
      </div>
    </section>
  );
}
