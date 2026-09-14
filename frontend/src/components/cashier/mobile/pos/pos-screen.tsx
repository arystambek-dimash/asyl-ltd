"use client";
import { History, QrCode, Send } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { formatCurrency } from "@/lib/utils";
import type { CashierModel } from "../../use-cashier";
import { BottomBar, type BottomBarItem } from "../bottom-bar";
import { TransactionsScreen } from "../transactions-screen";
import type { PosTab } from "./pos-logic";
import { PosResult } from "./pos-result";
import { PosAmountStep, PosClientStep, PosOrderStep, PosPhoneStep } from "./pos-steps";
import type { PosFlowApi } from "./use-pos-flow";

const TABS: BottomBarItem<PosTab>[] = [
  { key: "qr", label: "Оплата", icon: QrCode },
  { key: "remote", label: "Удаленно", icon: Send },
  { key: "history", label: "История", icon: History },
];
const TITLES: Record<PosTab, string> = { qr: "POS", remote: "Удаленная оплата", history: "История" };

function PosBody({ model, flow }: { model: CashierModel; flow: PosFlowApi }) {
  const { state, order } = flow;
  if (state.tab === "history") return <TransactionsScreen model={model} />;
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
        onOpenHistory={model.perms.canTransactions ? () => flow.setTab("history") : undefined}
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

/** POS кассы на телефоне, как Kaspi POS: оплата долга по QR, счёт на телефон и история — вкладками панели внизу. */
export function PosScreen({
  model,
  flow,
  section,
  onClose,
}: {
  model: CashierModel;
  flow: PosFlowApi;
  section?: string;
  onClose: () => void;
}) {
  const tabs = TABS.filter((tab) => tab.key !== "history" || model.perms.canTransactions);
  return (
    <AppShell
      title={TITLES[flow.state.tab]}
      section={section}
      back={flow.canGoBack ? { label: "Назад", onClick: flow.back } : { label: "Назад в кассу", onClick: onClose }}
      footer={
        <BottomBar label="Режим POS" items={tabs} active={flow.state.tab} disabled={flow.busy} onSelect={flow.setTab} />
      }
    >
      <PosBody model={model} flow={flow} />
    </AppShell>
  );
}
