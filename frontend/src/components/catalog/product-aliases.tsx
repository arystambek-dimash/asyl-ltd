"use client";
import { useState } from "react";
import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, apiError, apiErrorCode } from "@/lib/api";
import type { Product, ProductAlias } from "@/lib/types";

/** Коды товара в отчётах о вагонах только для чтения — колонка списка товаров. */
export function ProductAliasCodes({ aliases }: { aliases?: ProductAlias[] }) {
  if (!aliases?.length) return <span className="text-[var(--muted-foreground)]">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {aliases.map((alias) => (
        <span key={alias.id} className="rounded-md border px-1.5 py-0.5 font-mono text-xs">
          {alias.code}
        </span>
      ))}
    </span>
  );
}

/**
 * Коды товара в отчётах о вагонах («Д1с»): по ним бот и «Вставить отчёт»
 * узнают товар. Ответ сервера — свежий товар, экран применяет его сразу.
 * Код другого товара переносится только после явного «Перенести».
 */
export function ProductAliasesEditor({
  product,
  onChange,
}: {
  product: Product;
  onChange: (product: Product) => void;
}) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Код уже у другого товара: сервер спросил, переносить ли.
  const [taken, setTaken] = useState("");

  async function add(move = false) {
    if (!code.trim() || busy) return;
    setBusy(true);
    setError("");
    setTaken("");
    try {
      const { data } = await api.post<Product>(`/products/${product.id}/aliases/`, { code, move });
      onChange(data);
      setCode("");
    } catch (cause) {
      if (apiErrorCode(cause) === "alias_taken") setTaken(apiError(cause));
      else setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  async function remove(alias: ProductAlias) {
    setBusy(true);
    setError("");
    setTaken("");
    try {
      const { data } = await api.delete<Product>(`/products/${product.id}/aliases/${alias.id}/`);
      onChange(data);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  const aliases = product.aliases ?? [];
  return (
    <section className="flex flex-col gap-2 rounded-lg border p-3">
      <Label htmlFor="product-alias" className="mb-0">
        Коды в отчётах о вагонах
      </Label>
      <p className="text-xs text-[var(--muted-foreground)]">
        Как товар пишут в отчёте об отгрузке вагонов, например «Д1с». По коду бот и «Вставить отчёт» узнают товар.
      </p>
      {aliases.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {aliases.map((alias) => (
            <li
              key={alias.id}
              className="flex items-center gap-1 rounded-md border py-0.5 pl-2 pr-0.5 font-mono text-sm"
            >
              {alias.code}
              <button
                type="button"
                aria-label={`Убрать код ${alias.code}`}
                disabled={busy}
                onClick={() => void remove(alias)}
                className="inline-flex size-6 items-center justify-center rounded text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--destructive)] disabled:opacity-50"
              >
                <X className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <Input
          id="product-alias"
          placeholder="Код из отчёта"
          maxLength={64}
          value={code}
          onChange={(event) => {
            setCode(event.target.value);
            setTaken("");
          }}
          onKeyDown={(event) => {
            // Поле внутри формы товара: Enter добавляет код, а не сохраняет товар.
            if (event.key !== "Enter") return;
            event.preventDefault();
            void add();
          }}
        />
        <Button type="button" variant="outline" disabled={busy || !code.trim()} onClick={() => void add()}>
          <Plus className="size-4" /> Добавить
        </Button>
      </div>
      {taken && (
        <div role="alert" className="flex flex-wrap items-center gap-2 text-sm">
          <span>{taken}</span>
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void add(true)}>
            Перенести
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}
    </section>
  );
}
