"use client";
import { History, QrCode, Send } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { cn, formatCurrency } from "@/lib/utils";
import type { CashierModel } from "../../use-cashier";
import { TransactionsScreen } from "../transactions-screen";
import type { PosTab } from "./pos-logic";
import { PosResult } from "./pos-result";
import { PosAmountStep, PosClientStep, PosOrderStep, PosPhoneStep } from "./pos-steps";
import { usePosFlow, type PosFlowApi } from "./use-pos-flow";

const TABS: { key: PosTab; label: string; icon: React.ElementType }[] = [
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
        detail={flow.detail.data}
        loading={flow.detail.loading}
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

/** POS кассы на телефоне: оплата долга по Kaspi QR, счёт на телефон и история. */
export function PosScreen({ model, onClose }: { model: CashierModel; onClose: () => void }) {
  const { debts } = model;
  const flow = usePosFlow({ onPaid: () => void debts.reload() });
  const tabs = TABS.filter((tab) => tab.key !== "history" || model.perms.canTransactions);
  return (
    <AppShell
      title={TITLES[flow.state.tab]}
      section="Касса"
      back={{ label: flow.canGoBack ? "Назад" : "Закрыть POS", onClick: flow.canGoBack ? flow.back : onClose }}
    >
      <div className="pb-24">
        <PosBody model={model} flow={flow} />
      </div>
      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-[var(--border)] bg-[var(--card)] pb-[env(safe-area-inset-bottom)]">
        <div
          role="tablist"
          aria-label="Режим POS"
          className="mx-auto grid max-w-md"
          style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}
        >
          {tabs.map((tab) => {
            const active = flow.state.tab === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => flow.setTab(tab.key)}
                className={cn(
                  "m-1.5 flex flex-col items-center gap-0.5 rounded-lg py-2 text-[11px] font-medium transition-colors",
                  active ? "bg-[var(--muted)] text-[var(--foreground)]" : "text-[var(--muted-foreground)]",
                )}
              >
                <tab.icon className="size-5" aria-hidden />
                {tab.label}
              </button>
            );
          })}
        </div>
      </nav>
    </AppShell>
  );
}
