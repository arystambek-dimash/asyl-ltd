"use client";
import { useState } from "react";
import { BellRing, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { showSuccess } from "@/lib/toast";
import type { Client, Department, Me } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { formatDateTime, pluralRu } from "@/lib/utils";

const UNASSIGNED_CLIENTS_URL = "/clients/?department=none";

type OwnDepartment = Me["sales_department"];

/**
 * Клиенты саморегистрации приходят без отдела, и бухгалтерия отдела их не видит.
 * Плашка «ждут отдела» открывает окно, где их закрепляют за отделом.
 */
export function UnassignedClients({
  ownDepartment,
  onAssigned,
}: {
  ownDepartment: OwnDepartment;
  onAssigned: () => void;
}) {
  const [open, setOpen] = useState(false);
  // Плашке нужно только число — одна строка на страницу.
  const waiting = usePagedApi<Client>(UNASSIGNED_CLIENTS_URL, 1);
  const list = usePagedApi<Client>(open ? UNASSIGNED_CLIENTS_URL : null, 20);
  const { data: departments } = useApi<Department[]>(open && !ownDepartment ? "/departments/" : null);

  function assigned() {
    void waiting.reload();
    void list.reload();
    onAssigned();
  }

  if (!waiting.count && !open) return null;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mb-4 flex w-full items-center gap-3 rounded-xl bg-[var(--warning)]/10 px-4 py-3 text-left transition-colors hover:bg-[var(--warning)]/15"
      >
        <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-[var(--warning)]/15 text-[var(--warning)]">
          <BellRing className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold">
            {waiting.count} {pluralRu(waiting.count, ["клиент ждёт", "клиента ждут", "клиентов ждут"])} отдела
          </span>
          <span className="block text-xs text-[var(--muted-foreground)]">
            Зарегистрировались сами — пока их не закрепить, отделы их не видят
          </span>
        </span>
        <span className="flex items-center gap-1 text-sm font-medium">
          <span className="hidden sm:inline">Распределить</span>
          <ChevronRight className="size-4" />
        </span>
      </button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow="Клиенты"
        title="Ждут отдела"
        description={
          ownDepartment
            ? `Заберите своих клиентов в отдел «${ownDepartment.name}».`
            : "Закрепите каждого клиента за отделом продаж."
        }
        mobileFullscreen
      >
        {list.error && <ErrorAlert message={list.error} onRetry={list.reload} />}
        {!list.loading && !list.error && list.items.length === 0 && (
          <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Все клиенты распределены.</p>
        )}
        <ul className="divide-y">
          {list.items.map((client) => (
            <UnassignedClientRow
              key={client.id}
              client={client}
              ownDepartment={ownDepartment}
              departments={departments ?? []}
              onAssigned={assigned}
            />
          ))}
        </ul>
        <LoadMore
          shown={list.items.length}
          total={list.count}
          hasMore={list.hasMore}
          loading={list.loadingMore}
          onClick={list.loadMore}
        />
      </Modal>
    </>
  );
}

function UnassignedClientRow({
  client,
  ownDepartment,
  departments,
  onAssigned,
}: {
  client: Client;
  ownDepartment: OwnDepartment;
  departments: Department[];
  onAssigned: () => void;
}) {
  const [departmentId, setDepartmentId] = useState(ownDepartment ? String(ownDepartment.id) : "");
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const departmentName = ownDepartment?.name ?? departments.find((row) => String(row.id) === departmentId)?.name ?? "";

  async function assign() {
    setBusy(true);
    setError("");
    try {
      await api.post(`/clients/${client.id}/assign-department/`, { department: Number(departmentId) });
      showSuccess(`${client.name} — в отделе «${departmentName}»`);
      onAssigned();
    } catch (cause) {
      setError(apiError(cause));
      setAsking(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex flex-col gap-2 py-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{client.name}</div>
          <div className="truncate text-xs text-[var(--muted-foreground)] tabular-nums">
            {[client.company_name, client.phone, client.created_at && formatDateTime(client.created_at)]
              .filter(Boolean)
              .join(" · ")}
          </div>
        </div>
        {!asking && (
          <div className="flex shrink-0 gap-2">
            {!ownDepartment && (
              <Select
                aria-label={`Отдел для клиента ${client.name}`}
                className="h-9 sm:w-40"
                value={departmentId}
                onChange={(event) => setDepartmentId(event.target.value)}
              >
                <option value="">Отдел…</option>
                {departments.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.name}
                  </option>
                ))}
              </Select>
            )}
            <Button size="sm" className="h-9 shrink-0" disabled={!departmentId} onClick={() => setAsking(true)}>
              {ownDepartment ? "В мой отдел" : "Закрепить"}
            </Button>
          </div>
        )}
      </div>
      {asking && (
        <div
          role="alertdialog"
          aria-label={`Закрепить ${client.name}`}
          className="flex flex-col gap-2 rounded-lg bg-[var(--muted)] px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between"
        >
          <span className="text-sm">
            Закрепить за отделом «{departmentName}»? Заказы и оплаты клиента будут учитываться там.
          </span>
          <div className="flex shrink-0 justify-end gap-2">
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setAsking(false)}>
              Нет
            </Button>
            <Button size="sm" disabled={busy} onClick={assign}>
              {busy ? "Закрепляем…" : "Да"}
            </Button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}
    </li>
  );
}
