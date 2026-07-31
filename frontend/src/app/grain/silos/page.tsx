"use client";
import { useState } from "react";
import Link from "next/link";
import { ArrowLeft, Plus } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadMore } from "@/components/ui/load-more";
import { Modal } from "@/components/ui/modal";
import { ProgressBar } from "@/components/ui/progress-bar";
import { Select } from "@/components/ui/select";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { GRAIN_MOVEMENT_LABELS, formatKg } from "@/lib/grain";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";
import type { GrainMovement, GrainSilo } from "@/lib/types";

/* ── Создание силоса ────────────────────────────────────────────────────── */
function SiloForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [capacityTons, setCapacityTons] = useState("");
  const [culture, setCulture] = useState("");
  const [grainClass, setGrainClass] = useState("");
  const [line, setLine] = useState("");
  const [allowMixing, setAllowMixing] = useState(false);
  const [isQuarantine, setIsQuarantine] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post("/grain/silos/", {
        name,
        total_capacity_kg: Math.round(Number(capacityTons) * 1000),
        grain_culture: culture,
        grain_class: grainClass,
        unloading_line: line,
        allow_mixing: allowMixing,
        is_quarantine: isQuarantine,
      });
      onDone();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label>Название *</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Силос-3" />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Вместимость, т *</Label>
          <Input type="number" min="1" value={capacityTons} onChange={(e) => setCapacityTons(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Культура</Label>
          <Input value={culture} onChange={(e) => setCulture(e.target.value)} placeholder="пшеница" />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Класс</Label>
          <Input value={grainClass} onChange={(e) => setGrainClass(e.target.value)} placeholder="3" />
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label>Линия разгрузки</Label>
          <Input value={line} onChange={(e) => setLine(e.target.value)} placeholder="Линия 1" />
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={allowMixing} onChange={(e) => setAllowMixing(e.target.checked)} />
        Разрешено смешивание классов
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={isQuarantine} onChange={(e) => setIsQuarantine(e.target.checked)} />
        Карантинный силос
      </label>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2 border-t pt-3">
        <Button variant="outline" disabled={busy} onClick={onCancel}>
          Отмена
        </Button>
        <Button disabled={busy || !name.trim() || !capacityTons} onClick={() => void submit()}>
          {busy ? "Сохранение…" : "Создать силос"}
        </Button>
      </div>
    </div>
  );
}

/* ── Корректировка остатка ─────────────────────────────────────────────── */
function AdjustForm({ silo, onDone, onCancel }: { silo: GrainSilo; onDone: () => void; onCancel: () => void }) {
  const [deltaKg, setDeltaKg] = useState("");
  const [movementType, setMovementType] = useState("adjustment");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post(`/grain/silos/${silo.id}/adjust/`, {
        delta_kg: Number(deltaKg),
        movement_type: movementType,
        note,
      });
      onDone();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-[var(--muted-foreground)]">
        Остаток меняется только отдельной операцией — она попадает в историю движений.
      </p>
      <div className="flex flex-col gap-1.5">
        <Label>Тип операции</Label>
        <Select value={movementType} onChange={(e) => setMovementType(e.target.value)}>
          <option value="adjustment">Корректировка</option>
          <option value="inventory_correction">Инвентаризация</option>
          <option value="expense">Расход</option>
          <option value="transfer_in">Перемещение (в)</option>
          <option value="transfer_out">Перемещение (из)</option>
        </Select>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Изменение, кг (± целое) *</Label>
        <Input type="number" value={deltaKg} onChange={(e) => setDeltaKg(e.target.value)} placeholder="-1500" />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Причина *</Label>
        <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="акт инвентаризации №…" />
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2 border-t pt-3">
        <Button variant="outline" disabled={busy} onClick={onCancel}>
          Отмена
        </Button>
        <Button disabled={busy || !deltaKg || !note.trim()} onClick={() => void submit()}>
          {busy ? "Проведение…" : "Провести операцию"}
        </Button>
      </div>
    </div>
  );
}

/* ── История движений силоса ────────────────────────────────────────────── */
function MovementsModal({ silo, onClose }: { silo: GrainSilo; onClose: () => void }) {
  const movements = usePagedApi<GrainMovement>(`/grain/silos/${silo.id}/movements/`, 50);
  return (
    <Modal
      open
      onClose={onClose}
      eyebrow={`Силос «${silo.name}»`}
      title="История движений"
      className="max-w-2xl"
      footer={<Button onClick={onClose}>Закрыть</Button>}
    >
      {movements.error && <ErrorAlert message={movements.error} onRetry={() => void movements.reload()} />}
      <Table>
        <THead>
          <TR>
            <TH>Дата</TH>
            <TH>Операция</TH>
            <TH className="text-right">Изменение</TH>
            <TH className="text-right">Остаток</TH>
            <TH>Основание</TH>
          </TR>
        </THead>
        <TBody>
          {movements.items.length === 0 ? (
            <TR>
              <TD colSpan={5} className="py-8 text-center text-sm text-[var(--muted-foreground)]">
                Движений пока нет.
              </TD>
            </TR>
          ) : (
            movements.items.map((movement) => (
              <TR key={movement.id}>
                <TD className="tabular-nums">{formatDateTime(movement.created_at)}</TD>
                <TD>{GRAIN_MOVEMENT_LABELS[movement.movement_type] ?? movement.movement_type}</TD>
                <TD className="text-right tabular-nums font-medium">
                  {movement.delta_kg > 0 ? "+" : ""}
                  {formatKg(movement.delta_kg)}
                </TD>
                <TD className="text-right tabular-nums">{formatKg(movement.balance_after_kg)}</TD>
                <TD className="max-w-[220px] truncate text-xs text-[var(--muted-foreground)]">
                  {movement.wagon_number ? `Вагон ${movement.wagon_number} · ` : ""}
                  {movement.note}
                </TD>
              </TR>
            ))
          )}
        </TBody>
      </Table>
      <LoadMore
        shown={movements.items.length}
        total={movements.count}
        hasMore={movements.hasMore}
        loading={movements.loadingMore}
        onClick={movements.loadMore}
      />
    </Modal>
  );
}

function SilosPageInner() {
  const { me } = useAuth();
  const canAdmin = can(me, "grain.admin");
  const canAdjust = can(me, "grain.inventory");
  const { data: silos, error, reload } = useApi<GrainSilo[]>("/grain/silos/");
  const [createOpen, setCreateOpen] = useState(false);
  const [adjustFor, setAdjustFor] = useState<GrainSilo | null>(null);
  const [movementsFor, setMovementsFor] = useState<GrainSilo | null>(null);

  return (
    <AppShell
      title="Силосы"
      section="Работа"
      description="Расчётные остатки, резервы под вагоны и свободная вместимость."
      actions={
        <div className="flex items-center gap-2">
          <Link href="/grain" className={buttonVariants({ size: "sm", variant: "outline" })}>
            <ArrowLeft className="size-4" /> К вагонам
          </Link>
          {canAdmin && (
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <Plus className="size-4" /> Новый силос
            </Button>
          )}
        </div>
      }
    >
      {error && <ErrorAlert message={error} onRetry={reload} />}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {(silos ?? []).map((silo) => (
          <Card key={silo.id}>
            <CardHeader className="flex-row items-center justify-between p-4 pb-2">
              <CardTitle>{silo.name}</CardTitle>
              <div className="flex gap-1.5">
                {silo.is_quarantine && <Badge tone="destructive">карантин</Badge>}
                {silo.status !== "active" && (
                  <Badge tone="warning">{silo.status === "blocked" ? "заблокирован" : "обслуживание"}</Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 p-4 pt-2">
              <div>
                <div className="flex items-baseline justify-between text-sm">
                  <span className="tabular-nums font-semibold">{formatKg(silo.current_balance_kg)}</span>
                  <span className="text-xs text-[var(--muted-foreground)]">
                    из {formatKg(silo.total_capacity_kg)} · {silo.fill_percent}%
                  </span>
                </div>
                <ProgressBar pct={silo.fill_percent} className="mt-2" />
              </div>
              <div className="flex flex-col text-sm">
                <div className="flex justify-between border-b border-[var(--border)]/60 py-1.5">
                  <span className="text-[var(--muted-foreground)]">Зарезервировано</span>
                  <span className="tabular-nums">{formatKg(silo.reserved_kg)}</span>
                </div>
                <div className="flex justify-between border-b border-[var(--border)]/60 py-1.5">
                  <span className="text-[var(--muted-foreground)]">Свободно</span>
                  <span className="tabular-nums font-medium">{formatKg(silo.free_capacity_kg)}</span>
                </div>
                <div className="flex justify-between py-1.5">
                  <span className="text-[var(--muted-foreground)]">Культура</span>
                  <span>
                    {silo.grain_culture || "любая"}
                    {silo.grain_class ? ` · ${silo.grain_class}` : ""}
                    {silo.allow_mixing ? " · смешивание" : ""}
                  </span>
                </div>
                {silo.sensor_difference_kg != null && (
                  <div className="flex justify-between border-t border-[var(--border)]/60 py-1.5">
                    <span className="text-[var(--muted-foreground)]">Датчик vs расчёт</span>
                    <span className="tabular-nums">{formatKg(silo.sensor_difference_kg)}</span>
                  </div>
                )}
              </div>
              {silo.active_wagons.length > 0 && (
                <div className="flex flex-wrap gap-1.5 text-xs">
                  {silo.active_wagons.map((wagon) => (
                    <Link
                      key={wagon.id}
                      href={`/grain/wagons/${wagon.id}`}
                      className="rounded-full border px-2 py-0.5 text-[var(--ring)] hover:underline"
                    >
                      {wagon.number || `#${wagon.id}`}
                    </Link>
                  ))}
                </div>
              )}
              <div className="flex justify-end gap-2 border-t pt-3">
                <Button size="sm" variant="ghost" onClick={() => setMovementsFor(silo)}>
                  Движения
                </Button>
                {canAdjust && (
                  <Button size="sm" variant="outline" onClick={() => setAdjustFor(silo)}>
                    Корректировка
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
        {(silos ?? []).length === 0 && !error && (
          <Card className="sm:col-span-2 xl:col-span-3">
            <CardContent className="py-10 text-center text-sm text-[var(--muted-foreground)]">
              Силосов пока нет. {canAdmin ? "Создайте первый." : ""}
            </CardContent>
          </Card>
        )}
      </div>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} eyebrow="Зерно · Силос" title="Новый силос">
        {createOpen && (
          <SiloForm
            onCancel={() => setCreateOpen(false)}
            onDone={() => {
              setCreateOpen(false);
              void reload();
            }}
          />
        )}
      </Modal>
      <Modal
        open={!!adjustFor}
        onClose={() => setAdjustFor(null)}
        eyebrow={adjustFor ? `Силос «${adjustFor.name}»` : ""}
        title="Корректировка остатка"
      >
        {adjustFor && (
          <AdjustForm
            silo={adjustFor}
            onCancel={() => setAdjustFor(null)}
            onDone={() => {
              setAdjustFor(null);
              void reload();
            }}
          />
        )}
      </Modal>
      {movementsFor && <MovementsModal silo={movementsFor} onClose={() => setMovementsFor(null)} />}
    </AppShell>
  );
}

export default function GrainSilosPage() {
  return (
    <RequirePerm perm="grain.view" title="Силосы">
      <SilosPageInner />
    </RequirePerm>
  );
}
