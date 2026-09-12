import type { Payment } from "@/lib/types";
import { cn, currencySymbol, formatDateTime, formatMoney } from "@/lib/utils";

const STATUS_HELP: Record<string, { meaning: string; money: string; next: string }> = {
  requested: {
    meaning: "Счёт или клиентская заявка созданы, но поступление денег ещё не подтверждено.",
    money: "Не учитывается в оплаченной сумме заказа.",
    next: "Счёт провайдера подтвердится сам; заявку без платёжного сервиса проверит касса.",
  },
  received: {
    meaning: "Клиент сообщил об оплате без автоматического подтверждения сервиса.",
    money: "Пока не учитывается в оплаченной сумме заказа.",
    next: "Касса может подтвердить или отклонить операцию.",
  },
  confirmed: {
    meaning: "Оплата подтверждена и деньги поступили.",
    money: "Полностью учитывается в кассе и уменьшает долг заказа.",
    next: "Можно скачать выписку или оформить полный/частичный возврат.",
  },
  rejected: {
    meaning: "Операция отклонена и закрыта без оплаты.",
    money: "Не учитывается в кассе и не уменьшает долг.",
    next: "Если доступно восстановление, верните операцию в работу; иначе создайте новую.",
  },
  awaiting_customer: {
    meaning: "QR или счёт создан и ожидает действия клиента.",
    money: "Сумма зарезервирована, но ещё не считается оплаченной.",
    next: "Ничего подтверждать вручную не нужно — статус обновится автоматически.",
  },
  cancellation_pending: {
    meaning: "Запрос отмены телефонного счёта принят и ещё обрабатывается.",
    money: "Сумма остаётся зарезервированной до окончательного статуса.",
    next: "Дождитесь автоматической сверки с платёжным сервисом.",
  },
  payment_error: {
    meaning: "Платёжный сервис не смог обработать этот QR или счёт.",
    money: "Операция не учитывается как оплата.",
    next: "Создайте новую платёжную операцию и проверьте телефон клиента.",
  },
  refund_pending: {
    meaning: "Запрос возврата по счёту отправлен и ожидает результата.",
    money: "До подтверждения провайдера оплата ещё учитывается.",
    next: "Дождитесь подтверждения платёжного сервиса — статус обновится автоматически.",
  },
  partially_refunded: {
    meaning: "Клиенту возвращена часть оплаты.",
    money: "В кассе учитывается только остаток после возврата.",
    next: "Можно вернуть оставшуюся доступную сумму.",
  },
  refunded: {
    meaning: "Оплата возвращена клиенту полностью.",
    money: "Больше не учитывается в кассе и снова увеличивает остаток заказа.",
    next: "Повторный возврат для этой операции недоступен.",
  },
};

function StatusExplanation({ status }: { status: string }) {
  const help = STATUS_HELP[status] ?? {
    meaning: "Технический статус платёжной операции.",
    money: "Проверьте детали операции и историю возвратов.",
    next: "Обновите страницу для получения актуального состояния.",
  };
  return (
    <div className="space-y-2 text-sm">
      <div className="rounded-lg border px-3 py-2.5">
        <span className="font-medium">Что означает: </span>
        <span className="text-[var(--muted-foreground)]">{help.meaning}</span>
      </div>
      <div className="rounded-lg border px-3 py-2.5">
        <span className="font-medium">Деньги: </span>
        <span className="text-[var(--muted-foreground)]">{help.money}</span>
      </div>
      <div className="rounded-lg border px-3 py-2.5">
        <span className="font-medium">Что дальше: </span>
        <span className="text-[var(--muted-foreground)]">{help.next}</span>
      </div>
    </div>
  );
}

/** Содержимое окна «Статус операции»: сумма, смысл статуса, журнал операции, возвраты. */
export function TransactionDetail({ payment }: { payment: Payment }) {
  const steps = [
    {
      key: "created",
      label: "Создан",
      at: payment.paid_at,
      by: payment.recorded_by_name || "Система",
    },
    ...(payment.received_at
      ? [{ key: "received", label: "Принят кассой", at: payment.received_at, by: payment.received_by_name }]
      : []),
    ...(payment.confirmed_at
      ? [
          {
            key: "confirmed",
            label: payment.confirmation_mode === "automatic" ? "Подтверждён автоматически" : "Подтверждён",
            at: payment.confirmed_at,
            by: payment.confirmation_mode === "automatic" ? "Платёжный сервис" : payment.confirmed_by_name,
          },
        ]
      : []),
    ...(payment.status === "rejected" ? [{ key: "rejected", label: "Отклонён", at: null, by: null }] : []),
  ];
  return (
    <div className="space-y-4">
      <div className="rounded-xl border bg-[var(--muted)]/35 p-4">
        <div className="text-sm font-medium">
          {payment.client_name} · заказ #{payment.order}
        </div>
        <div className="mt-1 text-2xl font-semibold tabular-nums">
          {formatMoney(payment.amount)} {currencySymbol(payment.currency)}
        </div>
        <div className="mt-2 space-y-0.5 text-xs text-[var(--muted-foreground)]">
          {payment.provider && (
            <div>
              Состояние счёта: {payment.provider.status}
              {payment.provider.phone_number ? ` · ${payment.provider.phone_number}` : ""}
            </div>
          )}
          {payment.note && <div>Примечание: {payment.note}</div>}
        </div>
      </div>
      <StatusExplanation status={payment.effective_status ?? payment.status} />
      {/* Журнал операции — для любого способа, включая наличные: раньше
          историю имели только онлайн-счета, и наличная транзакция
          выглядела безымянной. */}
      <div>
        <div className="mb-2 text-sm font-medium">Журнал операции</div>
        <div className="space-y-1.5">
          {steps.map((step) => (
            <div key={step.key} className="flex items-baseline gap-2 text-sm">
              <span
                className={cn(
                  "size-1.5 shrink-0 translate-y-[-1px] rounded-full",
                  step.key === "rejected" ? "bg-[var(--destructive)]" : "bg-[var(--success)]",
                )}
              />
              <span className="font-medium">{step.label}</span>
              <span className="text-xs text-[var(--muted-foreground)]">
                {step.at ? formatDateTime(step.at) : ""}
                {step.by ? ` · ${step.by}` : ""}
              </span>
            </div>
          ))}
        </div>
      </div>
      {(payment.refunds?.length ?? 0) > 0 && (
        <div>
          <div className="mb-2 text-sm font-medium">История возвратов</div>
          <div className="space-y-2">
            {payment.refunds!.map((refund) => (
              <div key={refund.id} className="rounded-lg border px-3 py-2 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-medium">
                    {formatMoney(refund.amount)} {currencySymbol(payment.currency)}
                  </span>
                  <span className="text-xs text-[var(--muted-foreground)]">
                    {refund.status === "completed"
                      ? "Завершён"
                      : refund.status === "pending"
                        ? "В обработке"
                        : "Ошибка"}
                  </span>
                </div>
                <div className="mt-1 text-xs text-[var(--muted-foreground)]">
                  {refund.method === "apipay" ? "По счёту" : "Из кассы"} · {refund.reason}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
