"use client";
import { Fragment, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronDown, Plus, TrainFront } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadMore } from "@/components/ui/load-more";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { Tabs } from "@/components/ui/tabs";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { GRAIN_STATUS_TONE, formatKg } from "@/lib/grain";
import { usePagedApi } from "@/lib/use-paged-api";
import { cn, formatDateTime, formatIsoDate } from "@/lib/utils";
import { useAuth } from "@/store/auth";
import type { GrainSupply, GrainWagon } from "@/lib/types";

function WagonStatusBadge({ wagon }: { wagon: Pick<GrainWagon, "status" | "status_label"> }) {
  return (
    <Badge tone={GRAIN_STATUS_TONE[wagon.status] ?? "muted"} dot>
      {wagon.status_label}
    </Badge>
  );
}

/* ── Новая поставка ─────────────────────────────────────────────────────── */
function SupplyForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [supplier, setSupplier] = useState("");
  const [contract, setContract] = useState("");
  const [culture, setCulture] = useState("");
  const [grainClass, setGrainClass] = useState("");
  const [expectedDate, setExpectedDate] = useState("");
  const [expectedTons, setExpectedTons] = useState("");
  const [documentTons, setDocumentTons] = useState("");
  const [wagonsExpected, setWagonsExpected] = useState("");
  const [numbers, setNumbers] = useState("");
  const [note, setNote] = useState("");
  const [publish, setPublish] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const toKg = (tons: string) => (tons ? Math.round(Number(tons) * 1000) : null);
      const created = await api.post<GrainSupply>("/grain/supplies/", {
        supplier,
        contract,
        culture,
        grain_class: grainClass,
        expected_date: expectedDate || null,
        expected_total_kg: toKg(expectedTons),
        document_weight_kg: toKg(documentTons),
        wagons_expected: wagonsExpected ? Number(wagonsExpected) : null,
        note,
        wagon_numbers: numbers
          .split(/[\n,;]+/)
          .map((item) => item.trim())
          .filter(Boolean),
      });
      if (publish) await api.post(`/grain/supplies/${created.data.id}/publish/`);
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
          <Label>Поставщик *</Label>
          <Input value={supplier} onChange={(e) => setSupplier(e.target.value)} placeholder="ТОО Колос" />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Договор</Label>
          <Input value={contract} onChange={(e) => setContract(e.target.value)} placeholder="№ 12-2026" />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Культура *</Label>
          <Input value={culture} onChange={(e) => setCulture(e.target.value)} placeholder="пшеница" />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Класс / сорт</Label>
          <Input value={grainClass} onChange={(e) => setGrainClass(e.target.value)} placeholder="3" />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Ожидаемая дата</Label>
          <Input type="date" value={expectedDate} onChange={(e) => setExpectedDate(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Вагонов</Label>
          <Input
            type="number"
            min="1"
            value={wagonsExpected}
            onChange={(e) => setWagonsExpected(e.target.value)}
            placeholder="напр. 4"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Ожидаемый вес, т</Label>
          <Input
            type="number"
            min="0"
            value={expectedTons}
            onChange={(e) => setExpectedTons(e.target.value)}
            placeholder="272"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Вес по документам, т</Label>
          <Input
            type="number"
            min="0"
            value={documentTons}
            onChange={(e) => setDocumentTons(e.target.value)}
            placeholder="271.5"
          />
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Номера вагонов (можно позже)</Label>
        <Input
          value={numbers}
          onChange={(e) => setNumbers(e.target.value)}
          placeholder="через запятую: 94120001, 94120002"
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Комментарий</Label>
        <Input value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={publish} onChange={(e) => setPublish(e.target.checked)} />
        Сразу опубликовать (статус «Ожидается»)
      </label>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2 border-t pt-3">
        <Button variant="outline" disabled={busy} onClick={onCancel}>
          Отмена
        </Button>
        <Button disabled={busy || !supplier.trim() || !culture.trim()} onClick={() => void submit()}>
          {busy ? "Сохранение…" : "Создать поставку"}
        </Button>
      </div>
    </div>
  );
}

/* ── Прибытие вагона ────────────────────────────────────────────────────── */
function ArrivalForm({
  supplies,
  onDone,
  onCancel,
}: {
  supplies: GrainSupply[];
  onDone: (wagon: GrainWagon) => void;
  onCancel: () => void;
}) {
  const [number, setNumber] = useState("");
  const [supply, setSupply] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const res = await api.post<GrainWagon>("/grain/wagons/arrive/", {
        number,
        supply: supply || null,
      });
      onDone(res.data);
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label>Номер вагона *</Label>
        <Input value={number} onChange={(e) => setNumber(e.target.value)} placeholder="94120001" autoFocus />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Поставка (если номер не был заявлен)</Label>
        <Select value={supply} onChange={(e) => setSupply(e.target.value)}>
          <option value="">Определить по номеру</option>
          {supplies.map((item) => (
            <option key={item.id} value={item.id}>
              #{item.id} · {item.supplier} · {item.culture}
            </option>
          ))}
        </Select>
      </div>
      <p className="text-xs text-[var(--muted-foreground)]">
        Если вагон не найдётся среди ожидаемых, он попадёт в «незапланированные» и будет ждать подтверждения диспетчера.
      </p>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2 border-t pt-3">
        <Button variant="outline" disabled={busy} onClick={onCancel}>
          Отмена
        </Button>
        <Button disabled={busy || !number.trim()} onClick={() => void submit()}>
          {busy ? "Регистрация…" : "Зарегистрировать прибытие"}
        </Button>
      </div>
    </div>
  );
}

/* ── Ожидаемые поставки ─────────────────────────────────────────────────── */
function SuppliesTable({
  supplies,
  canSupply,
  onChanged,
}: {
  supplies: GrainSupply[];
  canSupply: boolean;
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");

  function toggle(id: number) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function publish(id: number) {
    setError("");
    try {
      await api.post(`/grain/supplies/${id}/publish/`);
      onChanged();
    } catch (e) {
      setError(apiError(e));
    }
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
      {error && <p className="px-4 pt-3 text-sm text-[var(--destructive)]">{error}</p>}
      <Table>
        <THead>
          <TR>
            <TH>Поставка</TH>
            <TH>Культура</TH>
            <TH>Дата</TH>
            <TH className="text-right">Ожидаемый вес</TH>
            <TH className="text-right">Вагоны</TH>
            <TH>Статус</TH>
            <TH />
          </TR>
        </THead>
        <TBody>
          {supplies.length === 0 ? (
            <TR>
              <TD colSpan={7} className="py-10 text-center text-sm text-[var(--muted-foreground)]">
                Ожидаемых поставок нет.
              </TD>
            </TR>
          ) : (
            supplies.map((supply) => {
              const open = expanded.has(supply.id);
              return (
                <Fragment key={supply.id}>
                  <TR
                    className="cursor-pointer transition-colors hover:bg-[var(--muted)]/40"
                    onClick={() => toggle(supply.id)}
                  >
                    <TD>
                      <button
                        type="button"
                        aria-expanded={open}
                        onClick={(event) => {
                          event.stopPropagation();
                          toggle(supply.id);
                        }}
                        className="flex items-center gap-1.5 font-medium"
                      >
                        <ChevronDown
                          className={cn(
                            "size-4 shrink-0 -rotate-90 text-[var(--muted-foreground)] transition-transform",
                            open && "rotate-0",
                          )}
                        />
                        #{supply.id} · {supply.supplier}
                      </button>
                      {supply.contract && (
                        <div className="pl-[22px] text-xs text-[var(--muted-foreground)]">{supply.contract}</div>
                      )}
                    </TD>
                    <TD>
                      {supply.culture}
                      {supply.grain_class ? ` · ${supply.grain_class} класс` : ""}
                    </TD>
                    <TD className="tabular-nums">{supply.expected_date ? formatIsoDate(supply.expected_date) : "—"}</TD>
                    <TD className="text-right tabular-nums">{formatKg(supply.expected_total_kg)}</TD>
                    <TD className="text-right tabular-nums">
                      {supply.wagons.length}
                      {supply.wagons_expected ? ` из ${supply.wagons_expected}` : ""}
                    </TD>
                    <TD>
                      {supply.status === "draft" ? (
                        <Badge tone="warning" dot>
                          Черновик
                        </Badge>
                      ) : (
                        <Badge tone="primary" dot>
                          Ожидается
                        </Badge>
                      )}
                    </TD>
                    <TD onClick={(event) => event.stopPropagation()}>
                      {supply.status === "draft" && canSupply && (
                        <Button size="sm" onClick={() => void publish(supply.id)}>
                          Опубликовать
                        </Button>
                      )}
                    </TD>
                  </TR>
                  {open && (
                    <TR className="bg-[var(--muted)]/30">
                      <TD colSpan={7} className="p-4">
                        {supply.wagons.length === 0 ? (
                          <p className="text-sm text-[var(--muted-foreground)]">
                            Номера вагонов ещё не заявлены — их можно добавить при прибытии.
                          </p>
                        ) : (
                          <div className="flex flex-col gap-1">
                            {supply.wagons.map((wagon) => (
                              <div key={wagon.id} className="flex flex-wrap items-center gap-3 text-sm">
                                <Link
                                  href={`/grain/wagons/${wagon.id}`}
                                  className="font-medium text-[var(--ring)] hover:underline"
                                >
                                  {wagon.number || `Вагон #${wagon.id}`}
                                </Link>
                                <WagonStatusBadge wagon={wagon} />
                                {wagon.net_weight_kg != null && (
                                  <span className="tabular-nums text-[var(--muted-foreground)]">
                                    нетто {formatKg(wagon.net_weight_kg)}
                                  </span>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                        {supply.note && <p className="mt-2 text-xs text-[var(--muted-foreground)]">{supply.note}</p>}
                      </TD>
                    </TR>
                  )}
                </Fragment>
              );
            })
          )}
        </TBody>
      </Table>
    </div>
  );
}

/* ── Вагоны (на территории / к выезду) ──────────────────────────────────── */
function WagonsTable({
  wagons,
  emptyText,
  showExit,
  onExit,
}: {
  wagons: GrainWagon[];
  emptyText: string;
  showExit?: boolean;
  onExit?: (wagon: GrainWagon) => void;
}) {
  const router = useRouter();
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
      <Table>
        <THead>
          <TR>
            <TH>Вагон</TH>
            <TH>Поставщик</TH>
            <TH>Культура</TH>
            <TH>Статус</TH>
            <TH className="text-right">Брутто</TH>
            <TH className="text-right">Нетто</TH>
            <TH>Силос</TH>
            <TH />
          </TR>
        </THead>
        <TBody>
          {wagons.length === 0 ? (
            <TR>
              <TD colSpan={8} className="py-10 text-center text-sm text-[var(--muted-foreground)]">
                {emptyText}
              </TD>
            </TR>
          ) : (
            wagons.map((wagon) => (
              <TR
                key={wagon.id}
                className="cursor-pointer transition-colors hover:bg-[var(--muted)]/40"
                onClick={() => router.push(`/grain/wagons/${wagon.id}`)}
              >
                <TD>
                  <span className="font-medium">{wagon.number || `#${wagon.id}`}</span>
                  {wagon.arrived_at && (
                    <div className="text-xs text-[var(--muted-foreground)]">{formatDateTime(wagon.arrived_at)}</div>
                  )}
                </TD>
                <TD>{wagon.supplier || "—"}</TD>
                <TD>
                  {wagon.culture || "—"}
                  {wagon.grain_class ? ` · ${wagon.grain_class}` : ""}
                </TD>
                <TD>
                  <WagonStatusBadge wagon={wagon} />
                </TD>
                <TD className="text-right tabular-nums">{formatKg(wagon.gross_weight_kg)}</TD>
                <TD className="text-right tabular-nums font-medium">{formatKg(wagon.net_weight_kg)}</TD>
                <TD>{wagon.assigned_silo_name ?? "—"}</TD>
                <TD onClick={(event) => event.stopPropagation()}>
                  <div className="flex justify-end gap-2">
                    {showExit && onExit && (
                      <Button size="sm" onClick={() => onExit(wagon)}>
                        Выпустить
                      </Button>
                    )}
                    <Link
                      href={`/grain/wagons/${wagon.id}`}
                      className={buttonVariants({ size: "sm", variant: "ghost" })}
                    >
                      Открыть
                    </Link>
                  </div>
                </TD>
              </TR>
            ))
          )}
        </TBody>
      </Table>
    </div>
  );
}

function GrainPageInner() {
  const { me } = useAuth();
  const canSupply = can(me, "grain.supply");
  const canArrive = can(me, "grain.arrive");
  const canExit = can(me, "grain.exit");
  const [tab, setTab] = useState<"expected" | "on_site" | "exit_ready" | "finished">("on_site");
  const [supplyOpen, setSupplyOpen] = useState(false);
  const [arriveOpen, setArriveOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");

  const supplies = usePagedApi<GrainSupply>(tab === "expected" ? "/grain/supplies/?status=expected" : null, 50);
  const drafts = usePagedApi<GrainSupply>(tab === "expected" ? "/grain/supplies/?status=draft" : null, 50);
  const wagons = usePagedApi<GrainWagon>(tab === "expected" ? null : `/grain/wagons/?scope=${tab}`, 50);
  const arrivalSupplies = usePagedApi<GrainSupply>(arriveOpen ? "/grain/supplies/?status=expected" : null, 100);

  function refreshAll() {
    void supplies.reload();
    void drafts.reload();
    void wagons.reload();
  }

  async function releaseWagon(wagon: GrainWagon) {
    setActionError("");
    try {
      await api.post(`/grain/wagons/${wagon.id}/exit/`);
      setNotice(`Вагон ${wagon.number} выехал.`);
      void wagons.reload();
    } catch (e) {
      setActionError(apiError(e));
    }
  }

  const supplyRows = [...drafts.items, ...supplies.items];

  return (
    <AppShell
      title="Приход зерна"
      section="Работа"
      description="Вагоны от заявки до выезда: взвешивание, лаборатория, силосы и оприходование."
      actions={
        <div className="flex items-center gap-2">
          <Link href="/grain/silos" className={buttonVariants({ size: "sm", variant: "outline" })}>
            Силосы
          </Link>
          {canArrive && (
            <Button size="sm" variant="outline" onClick={() => setArriveOpen(true)}>
              <TrainFront className="size-4" /> Прибытие
            </Button>
          )}
          {canSupply && (
            <Button size="sm" onClick={() => setSupplyOpen(true)}>
              <Plus className="size-4" /> Новая поставка
            </Button>
          )}
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        <Tabs
          tabs={[
            { key: "expected", label: "Ожидаются" },
            { key: "on_site", label: "На территории" },
            { key: "exit_ready", label: "Готовы к выезду" },
            { key: "finished", label: "Завершённые" },
          ]}
          active={tab}
          onChange={(key) => setTab(key as typeof tab)}
        />

        {notice && (
          <p className="rounded-lg border border-[var(--success)]/30 bg-[var(--success)]/10 px-3 py-2 text-sm text-[var(--success)]">
            {notice}
          </p>
        )}
        {actionError && <ErrorAlert message={actionError} onRetry={refreshAll} />}

        {tab === "expected" ? (
          <>
            {(supplies.error || drafts.error) && (
              <ErrorAlert message={supplies.error || drafts.error} onRetry={refreshAll} />
            )}
            <SuppliesTable supplies={supplyRows} canSupply={canSupply} onChanged={refreshAll} />
            <LoadMore
              shown={supplies.items.length}
              total={supplies.count}
              hasMore={supplies.hasMore}
              loading={supplies.loadingMore}
              onClick={supplies.loadMore}
            />
          </>
        ) : (
          <>
            {wagons.error && <ErrorAlert message={wagons.error} onRetry={() => void wagons.reload()} />}
            <WagonsTable
              wagons={wagons.items}
              emptyText={
                tab === "on_site"
                  ? "Вагонов на территории нет."
                  : tab === "exit_ready"
                    ? "Готовых к выезду вагонов нет."
                    : "Завершённых вагонов пока нет."
              }
              showExit={tab === "exit_ready" && canExit}
              onExit={releaseWagon}
            />
            <LoadMore
              shown={wagons.items.length}
              total={wagons.count}
              hasMore={wagons.hasMore}
              loading={wagons.loadingMore}
              onClick={wagons.loadMore}
            />
          </>
        )}
      </div>

      <Modal
        open={supplyOpen}
        onClose={() => setSupplyOpen(false)}
        eyebrow="Зерно · Заявка"
        title="Новая поставка"
        description="Неизвестные поля можно оставить пустыми и дополнить позже."
        className="max-w-2xl"
      >
        {supplyOpen && (
          <SupplyForm
            onCancel={() => setSupplyOpen(false)}
            onDone={() => {
              setSupplyOpen(false);
              setTab("expected");
              refreshAll();
            }}
          />
        )}
      </Modal>

      <Modal
        open={arriveOpen}
        onClose={() => setArriveOpen(false)}
        eyebrow="Зерно · Проходная"
        title="Прибытие вагона"
        description="Введите или отсканируйте номер вагона."
      >
        {arriveOpen && (
          <ArrivalForm
            supplies={arrivalSupplies.items}
            onCancel={() => setArriveOpen(false)}
            onDone={(wagon) => {
              setArriveOpen(false);
              setNotice(
                wagon.status === "waiting_for_approval"
                  ? `Вагон ${wagon.number} не найден в заявках — ждёт подтверждения диспетчера.`
                  : `Вагон ${wagon.number} зарегистрирован.`,
              );
              setTab("on_site");
              refreshAll();
            }}
          />
        )}
      </Modal>
    </AppShell>
  );
}

export default function GrainPage() {
  return (
    <RequirePerm perm="grain.view" title="Приход зерна">
      <GrainPageInner />
    </RequirePerm>
  );
}
