"use client";

import { useState } from "react";
import { Camera, Check, LoaderCircle, PackagePlus, Scale, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { apiFileUrl, formatKg } from "@/lib/grain";
import type { GrainUnassignedWeighing, GrainWagon } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { formatDateTime } from "@/lib/utils";

const CANDIDATES_URL = "/grain/wagons/?scope=on_site&direction=passage";

/** Панель работает поверх общего useApi; чужой или битый ответ просто не показывается. */
function isUnassignedWeighing(value: unknown): value is GrainUnassignedWeighing {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<GrainUnassignedWeighing>;
  return (
    typeof item.id === "number" &&
    typeof item.weight_kg === "number" &&
    typeof item.stable_weight_at === "string" &&
    !Number.isNaN(new Date(item.stable_weight_at).getTime())
  );
}

function isWagon(value: unknown): value is GrainWagon {
  return Boolean(value) && typeof value === "object" && typeof (value as GrainWagon).id === "number";
}

function candidateLabel(wagon: GrainWagon) {
  const stage =
    wagon.status === "arrived" && wagon.entry_weight_kg == null
      ? "ждёт вес пустой"
      : wagon.status === "at_silo" && wagon.exit_weight_kg == null
        ? `ждёт вес гружёной · заехала ${formatKg(wagon.entry_weight_kg)}`
        : wagon.status_label;
  return `${wagon.number || `#${wagon.id}`} · ${stage}`;
}

function isAwaitingWeight(wagon: GrainWagon) {
  return (
    (wagon.status === "arrived" && wagon.entry_weight_kg == null) ||
    (wagon.status === "at_silo" && wagon.exit_weight_kg == null)
  );
}

function UnassignedRow({
  item,
  candidates,
  canWeigh,
  onResolved,
}: {
  item: GrainUnassignedWeighing;
  candidates: GrainWagon[];
  canWeigh: boolean;
  onResolved: () => void;
}) {
  const [mode, setMode] = useState<"idle" | "assign" | "create" | "discard">("idle");
  const [wagonId, setWagonId] = useState("");
  const [number, setNumber] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const photo = apiFileUrl(item.photo_url);

  async function run(path: string, body: Record<string, unknown>) {
    setBusy(true);
    setError("");
    try {
      await api.post(`/grain/unassigned-weighings/${item.id}/${path}/`, body);
      setMode("idle");
      onResolved();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex flex-col gap-3 rounded-xl border bg-[var(--card)] p-3 sm:flex-row sm:items-start">
      <div className="w-full shrink-0 sm:w-44">
        {photo ? (
          <a
            href={photo}
            target="_blank"
            rel="noreferrer"
            className="block overflow-hidden rounded-lg border bg-black/5"
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- подписанная ссылка бэкенда */}
            <img src={photo} alt="Машина на весах" loading="lazy" className="aspect-video w-full object-cover" />
          </a>
        ) : (
          <div className="flex aspect-video w-full items-center justify-center rounded-lg border border-dashed text-xs text-[var(--muted-foreground)]">
            <Camera className="mr-1 size-3.5" /> кадра нет
          </div>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-lg font-semibold tabular-nums">{formatKg(item.weight_kg)}</span>
          <Badge tone="warning">номер не распознан</Badge>
          <span className="text-xs text-[var(--muted-foreground)]">
            {formatDateTime(item.stable_weight_at)} · камера {item.camera || "—"}
          </span>
        </div>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">
          На территории уже были машины, поэтому автоматика не стала угадывать, чей это вес. Привяжите его к рейсу или
          создайте новый.
        </p>

        {canWeigh && mode === "idle" && (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setMode("assign")}>
              <Scale /> Привязать к рейсу
            </Button>
            <Button size="sm" variant="outline" onClick={() => setMode("create")}>
              <PackagePlus /> Новый рейс
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setMode("discard")}>
              <Trash2 /> Отклонить
            </Button>
          </div>
        )}

        {mode === "assign" && (
          <form
            className="mt-3 flex flex-wrap items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void run("assign", { wagon: Number(wagonId) });
            }}
          >
            <Select
              aria-label="Рейс для привязки"
              value={wagonId}
              onChange={(event) => setWagonId(event.target.value)}
              className="h-9 min-w-64"
            >
              <option value="">Выберите рейс…</option>
              {candidates.filter(isAwaitingWeight).map((wagon) => (
                <option key={wagon.id} value={wagon.id}>
                  {candidateLabel(wagon)}
                </option>
              ))}
            </Select>
            <Button size="sm" type="submit" disabled={busy || !wagonId}>
              {busy ? <LoaderCircle className="animate-spin" /> : <Check />} Привязать
            </Button>
            <Button size="sm" type="button" variant="ghost" disabled={busy} onClick={() => setMode("idle")}>
              Отмена
            </Button>
          </form>
        )}

        {mode === "create" && (
          <form
            className="mt-3 flex flex-wrap items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void run("create-passage", { number, cargo_name: "" });
            }}
          >
            <Input
              aria-label="Номер машины"
              value={number}
              onChange={(event) => setNumber(event.target.value.toUpperCase())}
              placeholder="Номер (можно пустой)"
              className="h-9 w-48 font-mono uppercase"
              maxLength={30}
            />
            <Button size="sm" type="submit" disabled={busy}>
              {busy ? <LoaderCircle className="animate-spin" /> : <PackagePlus />} Создать и записать заезд
            </Button>
            <Button size="sm" type="button" variant="ghost" disabled={busy} onClick={() => setMode("idle")}>
              Отмена
            </Button>
          </form>
        )}

        {mode === "discard" && (
          <form
            className="mt-3 flex flex-wrap items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void run("discard", { reason });
            }}
          >
            <Input
              aria-label="Причина отклонения"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Причина (необязательно)"
              className="h-9 w-64"
              maxLength={200}
            />
            <Button size="sm" type="submit" variant="destructive" disabled={busy}>
              {busy ? <LoaderCircle className="animate-spin" /> : <Trash2 />} Отклонить
            </Button>
            <Button size="sm" type="button" variant="ghost" disabled={busy} onClick={() => setMode("idle")}>
              Отмена
            </Button>
          </form>
        )}

        {error && (
          <p role="alert" className="mt-2 text-xs text-[var(--destructive)]">
            {error}
          </p>
        )}
      </div>
    </li>
  );
}

/**
 * Веса автовесов, которые не удалось привязать без оператора. Панель сама
 * исчезает, когда очередь пуста, и обновляется тем же ритмом, что таблица.
 */
export function UnassignedWeighingsPanel({
  canWeigh,
  active = true,
  onChanged,
}: {
  canWeigh: boolean;
  active?: boolean;
  onChanged?: () => void;
}) {
  const { data, reload } = useApi<GrainUnassignedWeighing[]>("/grain/unassigned-weighings/");
  const { data: candidatesData, reload: reloadCandidates } = useApi<GrainWagon[] | { results: GrainWagon[] }>(
    CANDIDATES_URL,
  );
  useVisiblePolling(reload, 10_000, active);
  const items = Array.isArray(data) ? data.filter(isUnassignedWeighing) : [];
  const rawCandidates = Array.isArray(candidatesData) ? candidatesData : (candidatesData?.results ?? []);
  const candidates = Array.isArray(rawCandidates) ? rawCandidates.filter(isWagon) : [];
  if (!items.length) return null;

  return (
    <Card className="border-amber-200 bg-amber-50/40">
      <CardHeader className="p-4 pb-2">
        <CardTitle className="flex items-center gap-2">
          <Scale className="size-4 text-amber-700" /> Неопознанные взвешивания
          <Badge tone="warning">{items.length}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-1">
        <ul className="space-y-3">
          {items.map((item) => (
            <UnassignedRow
              key={item.id}
              item={item}
              candidates={candidates}
              canWeigh={canWeigh}
              onResolved={() => {
                void reload();
                void reloadCandidates();
                onChanged?.();
              }}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
