"use client";
import { useState } from "react";
import { AlertTriangle, KeyRound, Pencil, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ErrorAlert, FormError } from "@/components/ui/data-state";
import { api, apiError } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { useConfirmAction } from "@/lib/use-confirm-action";
import type { ApiPayAccount, Department } from "@/lib/types";
import { ApiPayAccountForm, DepartmentChips } from "./apipay-account-form";

/**
 * Вкладка «Kaspi · ApiPay» в настройке отделов (только суперюзеру): подключения
 * ApiPay и отделы каждого. Отдел переключается между подключениями одним
 * нажатием на пилюлю; отделы без Kaspi видны сразу сверху.
 */
export function ApiPayAccounts({
  departments,
  onChanged,
}: {
  departments: Department[];
  onChanged: () => Promise<void>;
}) {
  const { data, error: loadError, reload } = useApi<ApiPayAccount[]>("/apipay-accounts/");
  const accounts = data ?? [];
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const accountNames = new Map(accounts.map((account) => [account.id, account.name]));
  const missing = departments.filter((row) => row.is_active && !row.apipay_configured);

  async function refresh() {
    await reload();
    await onChanged();
  }

  const remove = useConfirmAction<ApiPayAccount>(async (account) => {
    await api.delete(`/apipay-accounts/${account.id}/`);
    await refresh();
  });

  async function setDepartments(account: ApiPayAccount, ids: number[]) {
    setBusy(true);
    setError("");
    try {
      await api.patch(`/apipay-accounts/${account.id}/`, { departments: ids });
      await refresh();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  function toggle(account: ApiPayAccount, departmentId: number) {
    const ids = account.departments.includes(departmentId)
      ? account.departments.filter((id) => id !== departmentId)
      : [...account.departments, departmentId];
    void setDepartments(account, ids);
  }

  const removing = remove.item;
  const removingNames = departments
    .filter((row) => removing?.departments.includes(row.id))
    .map((row) => row.name)
    .join(", ");

  return (
    <div className="flex flex-col gap-3">
      {loadError && <ErrorAlert message={loadError} onRetry={() => void reload()} />}
      {data && missing.length > 0 && (
        <div className="flex flex-col gap-3 rounded-xl border border-[var(--warning)]/40 bg-[var(--warning)]/10 p-3 sm:flex-row sm:items-center">
          <AlertTriangle className="size-4 shrink-0 text-[var(--warning)]" />
          <div className="min-w-0 flex-1 text-sm">
            <span className="font-medium">Без Kaspi: {missing.map((row) => row.name).join(", ")}.</span>{" "}
            <span className="text-[var(--muted-foreground)]">Счета и QR в этих отделах не выставляются.</span>
          </div>
          {accounts.length === 1 ? (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void setDepartments(accounts[0], [...accounts[0].departments, ...missing.map((row) => row.id)])
              }
            >
              Подключить к «{accounts[0].name}»
            </Button>
          ) : (
            <span className="text-xs text-[var(--muted-foreground)]">
              {accounts.length === 0 ? "Добавьте подключение ApiPay ниже." : "Отметьте их в нужном подключении ниже."}
            </span>
          )}
        </div>
      )}
      <FormError message={error} />

      {accounts.map((account) =>
        editing === account.id ? (
          <ApiPayAccountForm
            key={account.id}
            account={account}
            departments={departments}
            accountNames={accountNames}
            onCancel={() => setEditing(null)}
            onSaved={async () => {
              setEditing(null);
              await refresh();
            }}
          />
        ) : (
          <section key={account.id} className="rounded-xl border bg-[var(--card)]">
            <div className="flex items-start gap-3 p-4">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-[var(--muted)] text-[var(--muted-foreground)]">
                <KeyRound className="size-4" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold">{account.name}</div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--muted-foreground)]">
                  <span className="font-mono tabular-nums">{account.key_hint || "ключ не читается"}</span>
                  {account.webhook_configured ? (
                    <Badge tone="success">Вебхук настроен</Badge>
                  ) : (
                    <Badge tone="warning">Без секрета вебхука — оплата подтвердится сверкой</Badge>
                  )}
                </div>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setEditing(account.id)}
                aria-label={`Изменить ${account.name}`}
              >
                <Pencil className="size-3.5" />
                <span className="hidden sm:inline">Изменить</span>
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => remove.open(account)}
                aria-label={`Удалить ${account.name}`}
                className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>
            <div className="flex flex-col gap-2 border-t px-4 py-3">
              <div className="text-xs text-[var(--muted-foreground)]">Отделы этого подключения</div>
              <DepartmentChips
                departments={departments}
                selected={account.departments}
                accountId={account.id}
                accountNames={accountNames}
                disabled={busy}
                onToggle={(id) => toggle(account, id)}
              />
            </div>
          </section>
        ),
      )}

      {editing === "new" ? (
        <ApiPayAccountForm
          departments={departments}
          accountNames={accountNames}
          onCancel={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await refresh();
          }}
        />
      ) : (
        data && (
          <button
            type="button"
            onClick={() => setEditing("new")}
            className="flex items-center justify-center gap-2 rounded-xl border border-dashed p-4 text-sm font-medium text-[var(--muted-foreground)] transition-colors hover:border-[var(--foreground)]/40 hover:text-[var(--foreground)]"
          >
            <Plus className="size-4" /> {accounts.length === 0 ? "Подключить ApiPay" : "Ещё одно подключение"}
          </button>
        )
      )}

      <ConfirmDialog
        {...remove.dialog}
        title={`Удалить подключение «${removing?.name ?? ""}»?`}
        description={
          removingNames
            ? `${removingNames} останутся без Kaspi: счета и QR там перестанут выставляться.`
            : "Ни один отдел не использует это подключение."
        }
      />
    </div>
  );
}
