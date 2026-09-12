"use client";
import type { ReactNode } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { formatCurrency } from "@/lib/utils";
import type { CashierModel } from "../../use-cashier";
import { PosResult } from "./pos-result";
import { PosAmountStep, PosClientStep, PosOrderStep, PosPhoneStep } from "./pos-steps";
import type { PosFlowApi } from "./use-pos-flow";

function PosBody({
  model,
  flow,
  onOpenHistory,
}: {
  model: CashierModel;
  flow: PosFlowApi;
  onOpenHistory?: () => void;
}) {
  const { state, order } = flow;
  if (state.step === "result" && state.payment && flow.outcome) {
    return (
      <PosResult
        payment={state.payment}
        outcome={flow.outcome}
        clientName={state.clientName}
        error={state.error}
        onRetry={flow.retry}
        onNew={flow.reset}
      />
    );
  }
  if (state.step === "phone" && order) {
    return (
      <PosPhoneStep
        phone={state.phone}
        amount={Number(state.amount || "0")}
        error={state.error}
        busy={flow.busy}
        onChange={flow.setPhone}
        onSubmit={flow.sendInvoice}
      />
    );
  }
  if (state.step === "amount" && order) {
    const qr = state.flow === "qr";
    return (
      <PosAmountStep
        order={order}
        amount={state.amount}
        error={state.error}
        busy={flow.busy}
        submitLabel={qr ? `Показать QR · ${formatCurrency(Number(state.amount || "0"), "KZT")}` : "Далее"}
        block={flow.block}
        onOpenHistory={onOpenHistory}
        onDigit={flow.digit}
        onErase={flow.erase}
        onFillAll={flow.fillAll}
        onSubmit={qr ? flow.issueQr : flow.toPhone}
      />
    );
  }
  if (state.step !== "client") {
    return (
      <PosOrderStep
        clientName={state.clientName}
        orders={flow.orders}
        stores={flow.detail.data?.stores ?? []}
        loading={flow.detail.loading && !flow.detail.data}
        error={flow.detail.error}
        onRetry={() => void flow.detail.reload()}
        onPick={flow.pickOrder}
      />
    );
  }
  return (
    <PosClientStep
      rows={model.debtRows}
      loading={model.debts.loading}
      error={model.debts.error}
      onRetry={() => void model.debts.reload()}
      onPick={flow.pickClient}
    />
  );
}

/** POS кассы на телефоне: оплата долга по Kaspi QR или счёт на телефон; режим выбирает нижняя панель. */
export function PosScreen({
  model,
  flow,
  title,
  section,
  footer,
  onClose,
  onOpenHistory,
}: {
  model: CashierModel;
  flow: PosFlowApi;
  title: string;
  section?: string;
  /** Нижняя панель кассы — общая со всеми экранами. */
  footer?: ReactNode;
  onClose: () => void;
  onOpenHistory?: () => void;
}) {
  return (
    <AppShell
      title={title}
      section={section}
      back={flow.canGoBack ? { label: "Назад", onClick: flow.back } : { label: "Назад в кассу", onClick: onClose }}
    >
      <div className="pb-24">
        <PosBody model={model} flow={flow} onOpenHistory={onOpenHistory} />
      </div>
      {footer}
    </AppShell>
  );
}
