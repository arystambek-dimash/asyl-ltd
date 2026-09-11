"use client";

import Image from "next/image";
import { useId, useState, type FormEvent } from "react";
import { Camera, ChevronRight, Printer } from "lucide-react";
import { ColorDot, Panel } from "@/components/monoblock/ui";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { api, apiError } from "@/lib/api";
import { fullDay } from "@/lib/day-analytics";
import { colorMeta, normalizedColor } from "@/lib/monoblock-colors";
import {
  shippingIdentityError,
  shippingIdentityLabel,
  shippingSegmentPrintHref,
  type ShippingSegment,
  type ShippingSession,
  type ShippingSessionsPage,
} from "@/lib/shipping-sessions";
import type { AlwaysOnColorAnalytics } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn, formatDateTime, formatTime, pluralRu } from "@/lib/utils";

const INPUT_CLASS = "h-10 rounded-md border bg-[var(--background)] px-3 text-sm";
const countFormat = new Intl.NumberFormat("ru-RU");
// Сегодняшний список живой: мешки идут каждые несколько секунд.
const LIVE_POLL_MS = 3_000;
const UNCLASSIFIED_COLORS = new Set(["unclassified", "unknown"]);

function ManualSegmentNumber({ segment, reload }: { segment: ShippingSegment; reload: () => Promise<void> }) {
  const inputId = useId();
  const [number, setNumber] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [savedNumber, setSavedNumber] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving || !number.trim()) return;
    setSaving(true);
    setError("");
    try {
      const { data } = await api.post<ShippingSegment>(`/cameras/shipping-segments/${segment.id}/identify/`, {
        number: number.trim().toUpperCase(),
      });
      setSavedNumber(data.number);
      await reload();
    } catch (failure) {
      setError(apiError(failure) || "Не удалось сохранить номер. Проверьте права и обновите данные.");
    } finally {
      setSaving(false);
    }
  }
  if (savedNumber)
    return (
      <p role="status" className="text-sm">
        Номер {savedNumber} сохранён.
      </p>
    );
  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-2 rounded-lg bg-[var(--muted)]/50 p-3">
      <label htmlFor={inputId} className="block text-sm font-medium">
        Указать номер отрезка #{segment.id}
      </label>
      <div className="flex flex-wrap gap-2">
        <input
          id={inputId}
          className={cn(INPUT_CLASS, "min-w-0 flex-1 uppercase")}
          value={number}
          onChange={(event) => setNumber(event.target.value)}
          maxLength={20}
          autoComplete="off"
          placeholder="Номер машины или вагона"
          required
          disabled={saving}
        />
        <Button type="submit" size="sm" disabled={saving || !number.trim()}>
          {saving ? "Сохраняем…" : "Сохранить номер"}
        </Button>
      </div>
      <p className="text-xs text-[var(--muted-foreground)]">
        Сверьте номер с кадром. Количество мешков останется прежним.
      </p>
      {error && <ErrorAlert message={error} />}
    </form>
  );
}

function SegmentPhoto({ segment }: { segment: ShippingSegment }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  // List polling renews signed URLs, while the saved frame is immutable.
  // Reuse one URL for five minutes so live counts do not redownload photos.
  const [frame, setFrame] = useState(() => ({
    url: segment.photo_url,
    takenAt: segment.photo_taken_at,
    receivedAt: Date.now(),
  }));
  if (
    frame.url !== segment.photo_url &&
    (!frame.url ||
      !segment.photo_url ||
      frame.takenAt !== segment.photo_taken_at ||
      Date.now() - frame.receivedAt >= 300_000)
  ) {
    setFrame({ url: segment.photo_url, takenAt: segment.photo_taken_at, receivedAt: Date.now() });
  }
  if (!frame.url || failedUrl === frame.url) {
    return (
      <div className="flex aspect-video items-center justify-center gap-2 rounded-md border border-dashed text-xs text-[var(--muted-foreground)]">
        <Camera className="size-4" />
        Кадр недоступен
      </div>
    );
  }
  return (
    <a
      href={segment.photo_url || frame.url}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`Открыть кадр отрезка ${segment.id}`}
    >
      <Image
        src={frame.url}
        alt={`Кадр номера отрезка ${segment.id}`}
        width={480}
        height={270}
        unoptimized
        className="aspect-video w-full rounded-md bg-[var(--muted)] object-contain"
        onError={() => {
          if (segment.photo_url && segment.photo_url !== frame.url) {
            setFrame({ url: segment.photo_url, takenAt: segment.photo_taken_at, receivedAt: Date.now() });
          } else setFailedUrl(frame.url);
        }}
      />
    </a>
  );
}

function SegmentCard({
  segment,
  reload,
  stale,
}: {
  segment: ShippingSegment;
  reload: () => Promise<void>;
  stale: boolean;
}) {
  return (
    <article aria-label={`Отрезок ${segment.id}`} className="grid gap-4 border-t p-4 sm:grid-cols-[180px_1fr]">
      <div className="space-y-1.5">
        <SegmentPhoto segment={segment} />
        {segment.photo_taken_at && (
          <p className="text-xs text-[var(--muted-foreground)]">Кадр: {formatDateTime(segment.photo_taken_at)}</p>
        )}
      </div>
      <div className="min-w-0 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="font-medium">Отрезок #{segment.id}</h4>
          <Badge tone={segment.ended_at ? "muted" : "success"}>{segment.ended_at ? "Закрыт" : "Идёт подсчёт"}</Badge>
          <span className="ml-auto text-lg font-semibold tabular-nums">
            {countFormat.format(segment.total_bags)} меш.
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <strong>{segment.number || "Без номера"}</strong>
          <Badge tone={segment.number ? "muted" : "warning"}>{shippingIdentityLabel(segment)}</Badge>
        </div>
        <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
          <div>
            <dt className="text-[var(--muted-foreground)]">Начало</dt>
            <dd>{formatDateTime(segment.started_at)}</dd>
          </div>
          <div>
            <dt className="text-[var(--muted-foreground)]">Окончание</dt>
            <dd>{segment.ended_at ? formatDateTime(segment.ended_at) : "Ещё не завершён"}</dd>
          </div>
          <div>
            <dt className="text-[var(--muted-foreground)]">Последний мешок</dt>
            <dd>{formatDateTime(segment.last_counted_at)}</dd>
          </div>
          <div>
            <dt className="text-[var(--muted-foreground)]">Закрытие после простоя</dt>
            <dd>{segment.idle_timeout_seconds / 60} мин.</dd>
          </div>
        </dl>
        {!segment.number && segment.identity_error && (
          <p className="text-sm text-[var(--muted-foreground)]">
            {shippingIdentityError(segment.identity_error)} Подсчёт мешков сохранён.
          </p>
        )}
        {!segment.number && segment.can_identify && !stale && <ManualSegmentNumber segment={segment} reload={reload} />}
        <a
          href={shippingSegmentPrintHref(segment.id)}
          target="_blank"
          rel="noopener noreferrer"
          className={buttonVariants({ variant: "outline", size: "sm" })}
        >
          <Printer className="size-4" />
          Накладная отрезка
        </a>
      </div>
    </article>
  );
}

/** Цвета вагона: доли — полосой, точные количества — чипами. «Не определён» всегда в конце. */
function SessionColors({ colors }: { colors: AlwaysOnColorAnalytics[] }) {
  const ordered = [...colors].sort(
    (a, b) =>
      Number(UNCLASSIFIED_COLORS.has(normalizedColor(a.color))) -
      Number(UNCLASSIFIED_COLORS.has(normalizedColor(b.color))),
  );
  if (!ordered.length) return null;
  return (
    <div className="space-y-2">
      <div aria-hidden className="flex h-1.5 overflow-hidden rounded-full bg-[var(--muted)]">
        {ordered.map((item) => (
          <span
            key={item.color}
            className={cn("h-full", colorMeta(item.color).bar)}
            style={{ width: `${item.percent}%` }}
          />
        ))}
      </div>
      <ul aria-label="Цвета мешков" className="flex flex-wrap gap-x-4 gap-y-1">
        {ordered.map((item) => (
          <li key={item.color} title={`${item.percent}%`} className="flex items-center gap-1.5 text-xs">
            <ColorDot className={colorMeta(item.color).dot} />
            <span className="text-[var(--muted-foreground)]">{colorMeta(item.color).label}</span>{" "}
            <span className="font-medium tabular-nums">{countFormat.format(item.total)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function transportTitle(session: ShippingSession) {
  if (!session.number) return "Транспорт без номера";
  return `${session.recognition_model === "wagon_number" ? "Вагон" : "Машина"} ${session.number}`;
}

function SessionCard({
  session,
  reload,
  stale,
}: {
  session: ShippingSession;
  reload: () => Promise<void>;
  stale: boolean;
}) {
  // Сразу раскрыт только транспорт без номера: ему нужен ручной ввод с кадра.
  const [open, setOpen] = useState(!session.number);
  const detailsId = useId();
  const title = transportTitle(session);
  const parts = session.segments.length;
  const meta = [
    `${formatTime(session.started_at)}–${formatTime(session.ended_at ?? session.last_counted_at)}`,
    parts > 1 ? `${parts} ${pluralRu(parts, ["отрезок", "отрезка", "отрезков"])}` : null,
    session.order_id ? `Заказ #${session.order_id}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <article aria-label={title} className="overflow-hidden rounded-lg border bg-[var(--card)]">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={detailsId}
        onClick={() => setOpen(!open)}
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 pb-2 pt-3 text-left hover:bg-[var(--muted)]/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--ring)]"
      >
        <ChevronRight
          className={cn("size-4 shrink-0 text-[var(--muted-foreground)] transition-transform", open && "rotate-90")}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-semibold">{title}</span>
          <span className="block text-xs text-[var(--muted-foreground)]">{meta}</span>
        </span>
        {session.status === "active" && (
          <Badge tone="success" dot>
            Идёт погрузка
          </Badge>
        )}
        {!session.number && <Badge tone="warning">Без номера</Badge>}
        <span className="text-lg font-semibold tabular-nums">{countFormat.format(session.total_bags)} меш.</span>
      </button>
      <div className="px-4 pb-3 pl-11">
        <SessionColors colors={session.colors} />
      </div>
      {open && (
        <div id={detailsId}>
          {session.segments.map((segment) => (
            <SegmentCard key={segment.id} segment={segment} reload={reload} stale={stale} />
          ))}
        </div>
      )}
    </article>
  );
}

/** Одно слово на весь день: вагоны, машины или — если конвейер возил разное — сессии. */
function transportCount(sessions: ShippingSession[]) {
  const count = sessions.length;
  const models = new Set(sessions.map((session) => session.recognition_model));
  const only = models.size === 1 ? [...models][0] : null;
  const forms: [string, string, string] =
    only === "wagon_number"
      ? ["вагон", "вагона", "вагонов"]
      : only === "vehicle_number"
        ? ["машина", "машины", "машин"]
        : ["сессия", "сессии", "сессий"];
  return `${count} ${pluralRu(count, forms)}`;
}

/** Сессии отгрузки одной камеры за один день — раздел аналитики камеры. */
export function CameraShippingSessions({
  camera,
  day,
  today,
}: {
  camera: string;
  /** Выбранный день; null — период из нескольких дней, где день ещё не выбран. */
  day: string | null;
  today: string;
}) {
  const headingId = useId();
  const list = useApi<ShippingSessionsPage>(
    day ? `/cameras/shipping-sessions/?${new URLSearchParams({ camera, day })}` : null,
  );
  useVisiblePolling(list.reload, LIVE_POLL_MS, day === today);
  const failure = list.error || (list.errorStatus ? "Сессии недоступны. Проверьте права доступа." : "");
  const sessions = list.data?.results ?? [];
  const bags = sessions.reduce((sum, session) => sum + session.total_bags, 0);
  return (
    <section aria-labelledby={headingId}>
      <Panel className="p-5 sm:p-6">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h3 id={headingId} className="text-base font-semibold">
            Сессии отгрузки
          </h3>
          {day && <span className="text-sm text-[var(--muted-foreground)]">{fullDay(day)}</span>}
          {sessions.length > 0 && (
            <span className="ml-auto text-sm tabular-nums text-[var(--muted-foreground)]">
              {transportCount(sessions)} · {countFormat.format(bags)} меш.
            </span>
          )}
        </div>
        {!day ? (
          <p className="mt-2 text-sm text-[var(--muted-foreground)]">
            Выберите день на графике «Учтено по дням», чтобы увидеть сессии отгрузки за этот день.
          </p>
        ) : (
          <div className="mt-4 space-y-2">
            {failure && <ErrorAlert message={failure} onRetry={() => void list.reload()} />}
            {!list.data && !failure && <DataGate loading={list.loading} />}
            {list.data?.results.length === 0 && (
              <div className="rounded-lg bg-[var(--muted)]/35 px-4 py-6 text-center">
                <p className="font-medium">За этот день отгрузок нет</p>
                <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                  Сессия появляется сама с первым посчитанным мешком — даже без номера и заказа.
                </p>
              </div>
            )}
            {sessions.map((session) => (
              <SessionCard key={session.id} session={session} reload={list.reload} stale={!!failure} />
            ))}
            {list.data?.truncated && (
              <p className="text-xs text-[var(--muted-foreground)]">
                Показаны последние {sessions.length} {pluralRu(sessions.length, ["сессия", "сессии", "сессий"])} дня.
              </p>
            )}
          </div>
        )}
      </Panel>
    </section>
  );
}
