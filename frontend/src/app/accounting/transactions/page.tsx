"use client";

import { useMemo, useState } from "react";
import { Download, ExternalLink, RefreshCcw, RotateCcw, Search } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataGate } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, apiError } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { useApi } from "@/lib/use-api";
import { formatMoney } from "@/lib/utils";
import type { Payment } from "@/lib/types";

const STATUS: Record<string, { label: string; tone: "success" | "warning" | "destructive" | "muted" }> = {
  confirmed: { label: "Оплачено", tone: "success" },
  received: { label: "В кассе", tone: "warning" },
  requested: { label: "Ожидает", tone: "warning" },
  rejected: { label: "Отклонено", tone: "destructive" },
};

export default function TransactionsPage() {
  const { data, loading, error: loadError, reload } = useApi<Payment[]>("/payment-transactions/");
  const [query, setQuery] = useState("");
  const [refundFor, setRefundFor] = useState<Payment | null>(null);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return data ?? [];
    return (data ?? []).filter((row) =>
      `${row.id} ${row.order} ${row.client_name ?? ""} ${row.method_label ?? row.method}`.toLowerCase().includes(q),
    );
  }, [data, query]);

  async function receipt(payment: Payment) {
    const response = await api.get<Blob>(`/payment-transactions/${payment.id}/receipt/`, {
      responseType: "blob",
    });
    downloadBlob(response.data, `receipt_${payment.id}.pdf`);
  }

  async function refund() {
    if (!refundFor) return;
    setBusy(true);
    setError("");
    try {
      await api.post(`/payment-transactions/${refundFor.id}/refund/`, {
        amount: amount || undefined,
        reason,
      });
      setRefundFor(null);
      setAmount("");
      setReason("");
      await reload();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell title="Транзакции">
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <Card>
            <CardContent className="py-5">
              <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Операций</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">{data?.length ?? 0}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="py-5">
              <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Оплачено</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums text-[var(--success)]">
                {formatMoney(
                  (data ?? []).filter((p) => p.status === "confirmed").reduce((s, p) => s + Number(p.amount), 0),
                )}{" "}
                ₸
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="py-5">
              <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Возвращено</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">
                {formatMoney((data ?? []).reduce((s, p) => s + Number(p.provider?.total_refunded ?? 0), 0))} ₸
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader className="flex-row items-center justify-between gap-3">
            <CardTitle>Все платежи, возвраты и чеки</CardTitle>
            <Button variant="outline" size="sm" onClick={() => void reload()}>
              <RefreshCcw className="size-4" /> Обновить
            </Button>
          </CardHeader>
          <CardContent>
            <div className="relative mb-4 max-w-sm">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
              <Input
                className="pl-9"
                placeholder="Клиент, заказ или операция"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            {(loading || loadError) && <DataGate loading={loading} error={loadError} onRetry={reload} />}
            {!loading && !loadError && rows.length === 0 && (
              <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Транзакций пока нет.</p>
            )}
            {rows.length > 0 && (
              <Table>
                <THead>
                  <TR>
                    <TH>Операция</TH>
                    <TH>Клиент</TH>
                    <TH>Способ</TH>
                    <TH>Сумма</TH>
                    <TH>Статус</TH>
                    <TH>Возврат</TH>
                    <TH />
                  </TR>
                </THead>
                <TBody>
                  {rows.map((row) => {
                    const state = STATUS[row.status] ?? { label: row.status, tone: "muted" as const };
                    return (
                      <TR key={row.id}>
                        <TD>
                          <div className="font-medium">PAY-{String(row.id).padStart(6, "0")}</div>
                          <div className="text-xs text-[var(--muted-foreground)]">Заказ #{row.order}</div>
                        </TD>
                        <TD>{row.client_name ?? "—"}</TD>
                        <TD>
                          {row.method_label ?? row.method}
                          {row.provider?.channel === "qr" && (
                            <div className="text-xs text-[var(--muted-foreground)]">Kaspi QR</div>
                          )}
                        </TD>
                        <TD className="font-medium tabular-nums">
                          {formatMoney(row.amount)} {row.currency === "USD" ? "$" : "₸"}
                        </TD>
                        <TD>
                          <Badge tone={state.tone}>{state.label}</Badge>
                        </TD>
                        <TD>
                          {Number(row.provider?.total_refunded ?? 0) > 0 ? (
                            <span className="text-sm tabular-nums">{formatMoney(row.provider!.total_refunded)} ₸</span>
                          ) : (
                            "—"
                          )}
                        </TD>
                        <TD>
                          <div className="flex justify-end gap-1">
                            {row.provider?.qr_token_url && (
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => window.open(row.provider!.qr_token_url!, "_blank", "noopener")}
                              >
                                <ExternalLink className="size-4" />
                              </Button>
                            )}
                            <Button size="sm" variant="ghost" title="Скачать чек" onClick={() => void receipt(row)}>
                              <Download className="size-4" />
                            </Button>
                            {row.status === "confirmed" &&
                              row.provider?.channel === "phone" &&
                              Number(row.provider.available_for_refund) > 0 && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  title="Оформить возврат"
                                  onClick={() => {
                                    setRefundFor(row);
                                    setAmount(row.provider!.available_for_refund);
                                  }}
                                >
                                  <RotateCcw className="size-4" />
                                </Button>
                              )}
                          </div>
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      <Modal
        open={!!refundFor}
        onClose={() => !busy && setRefundFor(null)}
        eyebrow="ApiPay · Возврат"
        title="Вернуть оплату"
        description="Можно вернуть всю доступную сумму или указать часть."
        footer={
          <>
            <Button variant="outline" onClick={() => setRefundFor(null)}>
              Отмена
            </Button>
            <Button disabled={busy || !amount} onClick={() => void refund()}>
              {busy ? "Отправка…" : "Оформить возврат"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
          <div>
            <label className="mb-1.5 block text-sm">Сумма возврата</label>
            <Input type="number" min="0.01" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
          <div>
            <label className="mb-1.5 block text-sm">Причина</label>
            <Input
              maxLength={500}
              placeholder="Например: возврат товара"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
