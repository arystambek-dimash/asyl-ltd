"use client";

import Image from "next/image";
import { useId, useState, type FormEvent, type ReactNode } from "react";
import { Camera, ChevronDown, Printer, RefreshCw, Settings2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import {
  shippingIdentityError,
  shippingIdentityLabel,
  shippingSegmentPrintHref,
  type ShippingSegment,
  type ShippingSession,
  type ShippingSessionSettings,
  type ShippingSessionsPage,
} from "@/lib/shipping-sessions";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn, formatDateTime } from "@/lib/utils";

const INPUT_CLASS = "h-10 rounded-md border bg-[var(--background)] px-3 text-sm";
const countFormat = new Intl.NumberFormat("ru-RU");

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

function SessionCard({
  session,
  cameraName,
  reload,
  stale,
}: {
  session: ShippingSession;
  cameraName?: string;
  reload: () => Promise<void>;
  stale: boolean;
}) {
  const [open, setOpen] = useState(session.status === "active" || !session.number);
  const detailsId = useId();
  return (
    <article className="overflow-hidden rounded-xl border bg-[var(--card)]" aria-label={`Сессия ${session.id}`}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={detailsId}
        onClick={() => setOpen(!open)}
        className="flex w-full flex-wrap items-center gap-3 p-4 text-left hover:bg-[var(--muted)]/30"
      >
        <ChevronDown className={cn("size-4 shrink-0 transition-transform", !open && "-rotate-90")} />
        <div className="min-w-0 flex-1">
          <h3 className="text-lg font-semibold">
            {session.number
              ? `${session.recognition_model === "wagon_number" ? "Вагон" : "Машина"} ${session.number}`
              : "Транспорт без номера"}
          </h3>
          <p className="text-xs text-[var(--muted-foreground)]">
            {cameraName || session.camera} · сессия #{session.id} · {formatDateTime(session.started_at)}
          </p>
        </div>
        <Badge tone={session.status === "active" ? "success" : "muted"}>
          {session.status === "active" ? "Активна" : "Закрыта"}
        </Badge>
        {!session.number && <Badge tone="warning">Без номера</Badge>}
        <div className="ml-auto text-right">
          <p className="text-xl font-semibold tabular-nums">{countFormat.format(session.total_bags)} меш.</p>
          <p className="text-xs text-[var(--muted-foreground)]">Отрезков: {session.segments.length}</p>
        </div>
      </button>
      {open && (
        <div id={detailsId}>
          <p className="border-t px-4 py-2 text-xs text-[var(--muted-foreground)]">
            {session.order_id ? `Заказ #${session.order_id}` : "Без заказа · мешки учитываются в сессии"}
          </p>
          {session.segments.map((segment) => (
            <SegmentCard key={segment.id} segment={segment} reload={reload} stale={stale} />
          ))}
        </div>
      )}
    </article>
  );
}

function IdleSettings({
  data,
  saved,
}: {
  data: ShippingSessionSettings;
  saved: (next: ShippingSessionSettings) => void;
}) {
  const [open, setOpen] = useState(false);
  const [minutes, setMinutes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const inputId = useId();
  const seconds = Number(minutes) * 60;
  const valid = minutes.trim() !== "" && Number.isInteger(seconds) && seconds >= 30 && seconds <= 86_400;
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid || saving) return;
    setSaving(true);
    setError("");
    try {
      const response = await api.patch<ShippingSessionSettings>("/cameras/shipping-session-settings/", {
        idle_timeout_seconds: seconds,
      });
      saved(response.data);
      setOpen(false);
    } catch (failure) {
      setError(apiError(failure) || "Не удалось изменить настройку. Проверьте права и обновите данные.");
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          setMinutes(String(data.idle_timeout_seconds / 60));
          setError("");
          setOpen(true);
        }}
      >
        <Settings2 className="size-4" />
        Простой: {data.idle_timeout_seconds / 60} мин.
      </Button>
      <Modal
        open={open}
        onClose={() => {
          if (!saving) setOpen(false);
        }}
        title="Закрытие отрезка по простою"
      >
        <form onSubmit={(event) => void submit(event)} className="space-y-4">
          <p className="text-sm">
            Если мешки не поступают указанное время, отрезок закрывается автоматически. Следующий мешок начинает новый
            отрезок.
          </p>
          <label htmlFor={inputId} className="block text-sm font-medium">
            Простой, минут
          </label>
          <input
            id={inputId}
            type="number"
            min="0.5"
            max="1440"
            step="any"
            className={cn(INPUT_CLASS, "w-full")}
            value={minutes}
            onChange={(event) => setMinutes(event.target.value)}
            disabled={saving}
            required
          />
          <p className="text-xs text-[var(--muted-foreground)]">
            От 0,5 до 1440 минут. По умолчанию — 5 минут. Новое значение применяется к новым отрезкам; открытые
            сохраняют прежнюю настройку.
          </p>
          {error && <ErrorAlert message={error} />}
          <Button type="submit" disabled={saving || !valid}>
            {saving ? "Сохраняем…" : "Сохранить настройку"}
          </Button>
        </form>
      </Modal>
    </>
  );
}

function SessionList({
  camera,
  cameraNames,
  toolbar,
}: {
  camera: string;
  cameraNames: Map<string, string>;
  toolbar: ReactNode;
}) {
  const [cursors, setCursors] = useState<string[]>([""]);
  const query = new URLSearchParams();
  if (camera) query.set("camera", camera);
  const cursor = cursors[cursors.length - 1];
  if (cursor) query.set("cursor", cursor);
  const endpoint = `/cameras/shipping-sessions/${query.size ? `?${query}` : ""}`;
  const list = useApi<ShippingSessionsPage>(endpoint);
  useVisiblePolling(list.reload, 3_000);
  const failure = list.error || (list.errorStatus ? "Сессии недоступны. Проверьте права доступа." : "");
  return (
    <div className="space-y-3">
      <div
        role="group"
        aria-label="Фильтры и настройки сессий"
        className="flex flex-wrap items-center gap-2 border-b pb-3"
      >
        {toolbar}
        <Button
          className="ml-auto"
          variant="ghost"
          size="sm"
          disabled={list.loading}
          onClick={() => void list.reload()}
        >
          <RefreshCw className="size-4" />
          Обновить сессии
        </Button>
      </div>
      {failure && <ErrorAlert message={failure} onRetry={() => void list.reload()} />}
      {!list.data && !failure && <DataGate loading={list.loading} />}
      {list.data?.results.length === 0 && (
        <div className="rounded-lg bg-[var(--muted)]/35 px-4 py-6 text-center">
          <p className="font-medium">Отгрузок пока нет</p>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            Первый посчитанный мешок создаст отрезок автоматически, даже если номер ещё не распознан.
          </p>
        </div>
      )}
      {list.data?.results.map((session) => (
        <SessionCard
          key={session.id}
          session={session}
          cameraName={cameraNames.get(session.camera)}
          reload={list.reload}
          stale={!!failure}
        />
      ))}
      {(cursors.length > 1 || list.data?.next_cursor) && (
        <nav aria-label="Страницы сессий" className="flex items-center justify-between gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={cursors.length === 1 || list.loading}
            onClick={() => setCursors(cursors.slice(0, -1))}
          >
            Предыдущая страница
          </Button>
          <span className="text-sm">Страница {cursors.length}</span>
          <Button
            variant="outline"
            size="sm"
            disabled={!list.data?.next_cursor || list.loading}
            onClick={() => {
              const next = list.data?.next_cursor;
              if (next) setCursors([...cursors, String(next)]);
            }}
          >
            Следующая страница
          </Button>
        </nav>
      )}
    </div>
  );
}

export function ShippingSessionsPanel({ cameras = [] }: { cameras?: { src: string; name: string }[] }) {
  const [camera, setCamera] = useState("");
  const settings = useApi<ShippingSessionSettings>("/cameras/shipping-session-settings/");
  const cameraNames = new Map(cameras.map((item) => [item.src, item.name]));
  return (
    <Card role="region" aria-label="Сессии отгрузки" className="space-y-4 p-4 sm:p-5">
      <div>
        <h2 className="text-lg font-semibold">Сессии отгрузки</h2>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          Отрезки погрузки, номера и накладные. Подсчёт сохраняется и без заказа.
        </p>
      </div>
      {settings.error && <ErrorAlert message={settings.error} onRetry={() => void settings.reload()} />}
      <SessionList
        key={camera}
        camera={camera}
        cameraNames={cameraNames}
        toolbar={
          <>
            {cameras.length > 0 && (
              <label className="flex min-w-0 max-w-full items-center gap-2 text-sm">
                Конвейер
                <select
                  className={cn(INPUT_CLASS, "h-8 min-w-0 max-w-full")}
                  value={camera}
                  onChange={(event) => setCamera(event.target.value)}
                >
                  <option value="">Все камеры</option>
                  {cameras.map((item) => (
                    <option key={item.src} value={item.src}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {settings.data?.can_manage ? (
              <IdleSettings data={settings.data} saved={settings.setData} />
            ) : settings.data ? (
              <p className="text-sm text-[var(--muted-foreground)]">
                Закрытие по простою: {settings.data.idle_timeout_seconds / 60} мин.
              </p>
            ) : null}
          </>
        }
      />
    </Card>
  );
}
