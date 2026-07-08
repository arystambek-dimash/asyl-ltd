"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { formatPlate } from "@/components/ui/license-plate-input";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { FilterPills } from "@/components/ui/filter-pills";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { OrderForm } from "@/components/order-form";
import { DEPARTMENT_LABELS, ORDER_STATUS_LABELS, PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { formatMoney } from "@/lib/utils";
import { Pencil, Plus, Search } from "lucide-react";
import type { Order } from "@/lib/types";

// Позиции и цены редактируются до начала загрузки.
function isEditable(o: Order): boolean {
  return ["draft", "pending", "confirmed"].includes(o.status);
}

function OrdersPageInner() {
  const router = useRouter();
  const { data: orders, loading, reload } = useApi<Order[]>("/orders/");
  const { me } = useAuth();
  const canCreate = can(me, "orders.create");
  const canEdit = can(me, "orders.edit");
  // Сводная картина обоих отделов — руководителю/бухгалтеру/кассиру.
  const showDept = can(me, "dept2.view_all");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Order | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [dept, setDept] = useState("all");
  const [sortKey, setSortKey] = useState("id");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const list = (orders ?? []).filter((o) => dept === "all" || o.department === dept);
  const activeCount = list.filter(
    (o) => o.status !== "shipped" && o.status !== "cancelled"
  ).length;
  const totalSum = list.reduce((s, o) => s + Number(o.total_amount || 0), 0);

  const presentStatuses = Array.from(new Set(list.map((o) => o.status)));
  const pills = [
    { key: "all", label: "Все", count: list.length },
    ...presentStatuses.map((st) => ({
      key: st,
      label: ORDER_STATUS_LABELS[st] ?? st,
      count: list.filter((o) => o.status === st).length,
    })),
  ];

  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };

  const filtered = list.filter((o) => {
    if (status !== "all" && o.status !== status) return false;
    if (!q) return true;
    const hay = `${o.id} ${o.client_name ?? ""} ${o.truck_number ?? ""}`.toLowerCase();
    return hay.includes(q.toLowerCase());
  });

  const sorted = [...filtered].sort((a, b) => {
    let av: number | string, bv: number | string;
    if (sortKey === "amount") { av = Number(a.total_amount || 0); bv = Number(b.total_amount || 0); }
    else if (sortKey === "client") { av = a.client_name ?? ""; bv = b.client_name ?? ""; }
    else if (sortKey === "status") { av = a.status; bv = b.status; }
    else { av = a.id; bv = b.id; }
    const cmp = typeof av === "number" && typeof bv === "number"
      ? av - bv
      : String(av).localeCompare(String(bv), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <AppShell title="Заказы" section="Работа" description="Заказы клиентов: позиции, оплаты, машина и плановая дата прибытия на отгрузку."
      actions={canCreate ? (
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Новый заказ</span>
        </Button>
      ) : undefined}>
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Всего заказов" value={String(list.length)} />
        <StatCard label="В процессе" value={String(activeCount)} />
        <StatCard label="Сумма" value={`${formatMoney(totalSum)} ₸`} accent />
      </section>

      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="relative max-w-md flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по клиенту, номеру или #ID"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="flex items-center gap-2 overflow-x-auto">
          {showDept && (
            <FilterPills active={dept} onChange={setDept} items={[
              { key: "all", label: "Все отделы", count: (orders ?? []).length },
              { key: "main", label: DEPARTMENT_LABELS.main, count: (orders ?? []).filter((o) => o.department === "main").length },
              { key: "field", label: DEPARTMENT_LABELS.field, count: (orders ?? []).filter((o) => o.department === "field").length },
            ]} />
          )}
          <FilterPills items={pills} active={status} onChange={setStatus} />
        </div>
      </div>

      {/* Мобильные карточки: таблица на телефоне нечитаемая. */}
      <div className="flex flex-col gap-3 md:hidden">
        {loading ? (
          <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
        ) : sorted.length === 0 ? (
          <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Заказов пока нет.</p>
        ) : sorted.map((o) => (
          <div key={o.id} onClick={() => router.push(`/orders/${o.id}`)}
            className="flex cursor-pointer flex-col gap-2.5 rounded-xl border bg-[var(--card)] p-4 shadow-card">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold">#{o.id}</span>
                {showDept && (
                  <Badge tone={o.department === "field" ? "primary" : "muted"}>
                    {DEPARTMENT_LABELS[o.department ?? "main"] ?? o.department}
                  </Badge>
                )}
              </div>
              <StatusBadge status={o.status} dot />
            </div>
            <div className="text-sm font-medium">{o.client_name || `Клиент #${o.client}`}</div>
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <div className="text-[11px] text-[var(--muted-foreground)]">Сумма</div>
                <div className="font-semibold tabular-nums">{formatMoney(o.total_amount)} ₸</div>
              </div>
              <div>
                <div className="text-[11px] text-[var(--muted-foreground)]">Оплачено</div>
                <div className="tabular-nums">{formatMoney(o.paid_total)} ₸</div>
              </div>
              {o.truck_number && (
                <div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">Машина</div>
                  <div className="tabular-nums">{formatPlate(o.truck_number)}</div>
                </div>
              )}
              {o.arrival_date && (
                <div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">Прибытие</div>
                  <div>{new Date(o.arrival_date).toLocaleDateString("ru-RU")}</div>
                </div>
              )}
            </div>
            <div className="flex items-center justify-between border-t pt-2">
              {o.status === "shipped" && o.payment_status ? (
                <Badge tone={PAYMENT_STATUS_TONE[o.payment_status] ?? "muted"}>
                  {PAYMENT_STATUS_LABELS[o.payment_status] ?? o.payment_status}
                </Badge>
              ) : <span />}
              {canEdit && isEditable(o) && (
                <Button size="sm" variant="outline"
                  onClick={(e) => { e.stopPropagation(); setEditing(o); }}>
                  <Pencil className="size-3.5" /> Изменить
                </Button>
              )}
            </div>
          </div>
        ))}
      </div>

      <Card className="hidden md:block">
        <CardContent className="pt-6">
          {loading ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
          ) : (
            <Table>
              <THead>
                <TR>
                  <SortableHeader label="№" sortKey="id" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  {showDept && <TH>Отдел</TH>}
                  <SortableHeader label="Клиент" sortKey="client" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <TH>Машина</TH>
                  <TH>Прибытие</TH>
                  <SortableHeader label="Сумма" sortKey="amount" activeKey={sortKey} dir={sortDir} onClick={toggleSort} align="right" />
                  <TH>Оплачено</TH>
                  <SortableHeader label="Статус" sortKey="status" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  {canEdit && <TH></TH>}
                </TR>
              </THead>
              <TBody>
                {sorted.map((o) => (
                  <TR key={o.id} className="cursor-pointer"
                    onClick={() => router.push(`/orders/${o.id}`)}>
                    <TD className="font-medium">
                      <Link href={`/orders/${o.id}`} className="hover:underline"
                        onClick={(e) => e.stopPropagation()}>#{o.id}</Link>
                    </TD>
                    {showDept && (
                      <TD>
                        <Badge tone={o.department === "field" ? "primary" : "muted"}>
                          {DEPARTMENT_LABELS[o.department ?? "main"] ?? o.department}
                        </Badge>
                      </TD>
                    )}
                    <TD>{o.client_name || `Клиент #${o.client}`}</TD>
                    <TD className="font-medium tabular-nums">{o.truck_number ? formatPlate(o.truck_number) : "—"}</TD>
                    <TD>{o.arrival_date ? new Date(o.arrival_date).toLocaleDateString("ru-RU") : "—"}</TD>
                    <TD className="text-right tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                    <TD className="tabular-nums text-[var(--muted-foreground)]">{formatMoney(o.paid_total)} ₸</TD>
                    <TD>
                      <div className="flex items-center gap-1.5">
                        <StatusBadge status={o.status} dot />
                        {o.status === "shipped" && o.payment_status && (
                          <Badge tone={PAYMENT_STATUS_TONE[o.payment_status] ?? "muted"}>
                            {PAYMENT_STATUS_LABELS[o.payment_status] ?? o.payment_status}
                          </Badge>
                        )}
                      </div>
                    </TD>
                    {canEdit && (
                      <TD onClick={(e) => e.stopPropagation()}>
                        {isEditable(o) && (
                          <Button size="sm" variant="ghost" title="Изменить заказ"
                            onClick={() => setEditing(o)}>
                            <Pencil className="size-4" />
                          </Button>
                        )}
                      </TD>
                    )}
                  </TR>
                ))}
                {sorted.length === 0 && (
                  <TR><TD colSpan={(showDept ? 8 : 7) + (canEdit ? 1 : 0)} className="py-4 text-center text-[var(--muted-foreground)]">
                    Заказов пока нет.</TD></TR>)}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Работа · Заказ"
        title="Новый заказ"
        description="Отдел, клиент, позиции и плановая дата прибытия."
        className="max-w-2xl">
        {open && <OrderForm onCancel={() => setOpen(false)}
          onDone={() => { setOpen(false); reload(); }} />}
      </Modal>

      <Modal open={!!editing} onClose={() => setEditing(null)}
        eyebrow={editing ? `Работа · Заказ #${editing.id}` : "Работа · Заказ"}
        title="Изменить заказ"
        description="Позиции, цены, машина и дата прибытия. Изменения фиксируются в журнале."
        className="max-w-2xl">
        {editing && <OrderForm editing={editing}
          onCancel={() => setEditing(null)}
          onDone={() => { setEditing(null); reload(); }} />}
      </Modal>
    </AppShell>
  );
}

export default function OrdersPage() {
  return <RequirePerm perm="orders.view" title="Заказы"><OrdersPageInner /></RequirePerm>;
}
