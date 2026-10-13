"use client";
import { useState, type FormEvent } from "react";
import { Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { FormError } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import { api, apiError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import type { ApiPayAccount, Department } from "@/lib/types";

/** Отделы-пилюли подключения: нажатая — отдел принимает оплату через это подключение. */
export function DepartmentChips({
  departments,
  selected,
  accountId,
  accountNames,
  disabled,
  onToggle,
}: {
  departments: Department[];
  selected: number[];
  /** Подключение, для которого выбираются отделы; null — новое. */
  accountId: number | null;
  accountNames: Map<number, string>;
  disabled?: boolean;
  onToggle: (departmentId: number) => void;
}) {
  if (departments.length === 0) {
    return <p className="text-xs text-[var(--muted-foreground)]">Сначала создайте отдел на вкладке «Отделы».</p>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {departments.map((department) => {
        const active = selected.includes(department.id);
        const elsewhere =
          !active && department.apipay_account !== null && department.apipay_account !== accountId
            ? accountNames.get(department.apipay_account)
            : undefined;
        return (
          <Chip
            key={department.id}
            active={active}
            onClick={() => !disabled && onToggle(department.id)}
            className={cn(!department.is_active && "opacity-60", disabled && "pointer-events-none")}
          >
            <span className="size-2 rounded-full" style={{ backgroundColor: department.color }} />
            {department.name}
            {elsewhere && <span className="font-normal text-[var(--muted-foreground)]">· сейчас {elsewhere}</span>}
          </Chip>
        );
      })}
    </div>
  );
}

function WebhookHelp() {
  const url = `${typeof window === "undefined" ? "" : window.location.origin}/api/webhooks/apipay/`;
  const [copied, setCopied] = useState(false);
  return (
    <ol className="flex list-decimal flex-col gap-1.5 rounded-lg bg-[var(--muted)]/50 py-3 pr-3 pl-7 text-xs text-[var(--muted-foreground)]">
      <li>В кабинете apipay.kz скопируйте API-ключ.</li>
      <li>
        В настройках вебхука укажите адрес
        <span className="mt-1 flex items-center gap-2">
          <code className="min-w-0 truncate rounded bg-[var(--card)] px-1.5 py-0.5 text-[var(--foreground)]">
            {url}
          </code>
          <button
            type="button"
            onClick={async () => setCopied(await copyText(url))}
            className="inline-flex shrink-0 items-center gap-1 font-medium text-[var(--foreground)] hover:underline"
          >
            <Copy className="size-3" /> {copied ? "Скопировано" : "Скопировать"}
          </button>
        </span>
      </li>
      <li>Вставьте сюда секрет вебхука из того же кабинета.</li>
    </ol>
  );
}

/**
 * Новое подключение ApiPay или правка существующего. Сервер ключи не
 * возвращает: при правке пустое поле значит «оставить как было».
 */
export function ApiPayAccountForm({
  account,
  departments,
  accountNames,
  onCancel,
  onSaved,
}: {
  account?: ApiPayAccount;
  departments: Department[];
  accountNames: Map<number, string>;
  onCancel: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(account?.name ?? "");
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  // Новое подключение сразу забирает отделы, которые сейчас без Kaspi.
  const [selected, setSelected] = useState<number[]>(
    account?.departments ?? departments.filter((row) => row.is_active && !row.apipay_configured).map((row) => row.id),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const canSave = name.trim() !== "" && (account !== undefined || key.trim() !== "");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const payload: Record<string, unknown> = { name: name.trim(), departments: selected };
    if (key.trim()) payload.api_key = key.trim();
    if (secret.trim()) payload.webhook_secret = secret.trim();
    setBusy(true);
    setError("");
    try {
      if (account) await api.patch(`/apipay-accounts/${account.id}/`, payload);
      else await api.post("/apipay-accounts/", payload);
      await onSaved();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  const keepHint = account ? "Оставьте пустым, чтобы не менять" : undefined;
  return (
    <form onSubmit={submit} className="flex flex-col gap-4 rounded-xl border bg-[var(--card)] p-4">
      <div>
        <div className="text-sm font-semibold">
          {account ? `Изменить «${account.name}»` : "Новое подключение ApiPay"}
        </div>
        <div className="text-xs text-[var(--muted-foreground)]">
          Один кабинет ApiPay может принимать оплату сразу за несколько отделов.
        </div>
      </div>
      <label className="flex flex-col gap-1 text-xs font-medium">
        Название
        <Input
          autoFocus={!account}
          value={name}
          maxLength={100}
          onChange={(event) => setName(event.target.value)}
          placeholder="Например, Kaspi Мельница"
        />
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs font-medium">
          API-ключ
          <PasswordInput
            aria-label="API-ключ"
            autoComplete="off"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder={account ? `${account.key_hint} · ${keepHint}` : undefined}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium">
          Секрет вебхука
          <PasswordInput
            aria-label="Секрет вебхука"
            autoComplete="off"
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            placeholder={account?.webhook_configured ? keepHint : undefined}
          />
        </label>
      </div>
      <div className="flex flex-col gap-2">
        <div className="text-xs font-medium">Отделы, которые принимают оплату через это подключение</div>
        <DepartmentChips
          departments={departments}
          selected={selected}
          accountId={account?.id ?? null}
          accountNames={accountNames}
          onToggle={(id) =>
            setSelected((current) => (current.includes(id) ? current.filter((row) => row !== id) : [...current, id]))
          }
        />
      </div>
      <WebhookHelp />
      <FormError message={error} />
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
          Отмена
        </Button>
        <Button type="submit" size="sm" disabled={busy || !canSave}>
          {busy ? "Сохранение…" : account ? "Сохранить" : "Подключить"}
        </Button>
      </div>
    </form>
  );
}
