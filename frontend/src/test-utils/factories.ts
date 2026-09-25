import type { LoaderOrder } from "@/lib/loader";
import type { Department, Me, Order, Payment, QrRefundState, ReportDay } from "@/lib/types";
import type { BotMessage, WhatsAppBotSettings, WhatsAppBotStatus } from "@/lib/whatsapp-bot";

/** Сотрудник без прав и отдела; в тесте переопределяются только важные ему поля. */
export function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    id: 1,
    username: "user",
    is_client: false,
    is_superuser: false,
    permissions: [],
    position: null,
    sales_department: null,
    ...overrides,
  };
}

/** Подтверждённый заказ на фуру без позиций и сумм. */
export function makeOrder(overrides: Partial<Order> = {}): Order {
  return {
    id: 1,
    client: 1,
    client_name: "Клиент",
    warehouse: 1,
    warehouse_name: "Склад 1",
    currency: "KZT",
    status: "confirmed",
    transport_type: "truck",
    truck_number: "",
    items: [],
    total_amount: "0.00",
    paid_total: "0.00",
    remaining_amount: "0.00",
    is_fully_paid: true,
    bag_estimate_kg: "0.00",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

/** Строка очереди грузчика: подтверждённая фура на 16.09, один мешок, не оплачена. */
export function makeLoaderOrder(id: number, overrides: Partial<LoaderOrder> = {}): LoaderOrder {
  return {
    id,
    status: "confirmed",
    transport_type: "truck",
    truck_number: "",
    currency: "KZT",
    planned_on: "2026-09-16",
    client_name: "Клиент",
    items: [],
    bags: 1,
    total_kg: "50.00",
    total_amount: "1000.00",
    shipped_at: null,
    trailer_number: "",
    transport_suggestions: [],
    transport_locked: false,
    client_country: "KZ",
    payment_status: "unpaid",
    remaining_amount: "1000.00",
    can_rollback: false,
    rail_station: "",
    wagons: [],
    report_sent_at: null,
    report_sent_to: "",
    report_status: "",
    report_error: "",
    ...overrides,
  };
}

/** Отдел по умолчанию, Kaspi не подключён. */
export function makeDepartment(overrides: Partial<Department> = {}): Department {
  return {
    id: 1,
    code: "mill",
    name: "Мельница",
    color: "#315FD5",
    is_active: true,
    is_default: true,
    order_count: 0,
    created_at: "2026-01-01T00:00:00Z",
    apipay_configured: false,
    apipay_webhook_configured: false,
    apipay_updated_at: null,
    ...overrides,
  };
}

/** Подтверждённая оплата наличными на 100 000 ₸, доступная к возврату целиком. */
export function makePayment(overrides: Partial<Payment> = {}): Payment {
  return {
    id: 5,
    order: 1,
    currency: "KZT",
    amount: "100000.00",
    method: "cash",
    status: "confirmed",
    paid_at: "2026-09-20T10:00:00Z",
    available_for_refund: "100000.00",
    ...overrides,
  } as Payment;
}

/** Возврат Kaspi QR на 5 000 ₸, ждёт, пока покупатель откроет ссылку. */
export function makeQrRefund(overrides: Partial<QrRefundState> = {}): QrRefundState {
  return {
    id: 1,
    status: "awaiting_customer",
    active: true,
    amount: "5000.00",
    refunded_amount: null,
    client_name: null,
    customer_url: "https://qr.apipay.kz/refund/token",
    link_expires_at: "2026-09-18T10:00:00+05:00",
    operations: [],
    receipt_url: null,
    error_code: null,
    error_message: null,
    created_at: "2026-09-17T10:00:00+05:00",
    ...overrides,
  };
}

/** День отчёта без отгрузок и оплат. */
export function makeReportDay(overrides: Partial<ReportDay> = {}): ReportDay {
  return {
    date: "2026-07-01",
    orders: 0,
    bags: 0,
    revenue: "0.00",
    paid_amount: "0.00",
    debt_amount: "0.00",
    cash: "0.00",
    cashless: "0.00",
    refunded: "0.00",
    received: "0.00",
    payments: 0,
    refunds: 0,
    revenue_by_currency: {},
    paid_amount_by_currency: {},
    debt_amount_by_currency: {},
    cash_by_currency: {},
    cashless_by_currency: {},
    refunded_by_currency: {},
    received_by_currency: {},
    ...overrides,
  };
}

/** Настройки бота WhatsApp: включён, чаты и отправители не ограничены, отчёты — Динаре. */
export function makeBotSettings(overrides: Partial<WhatsAppBotSettings> = {}): WhatsAppBotSettings {
  return {
    enabled: true,
    allowed_chat_ids: [],
    allowed_sender_ids: [],
    show_amounts_in_reply: false,
    duplicate_window_days: 3,
    price_tolerance_pct: "15.00",
    report_recipient_name: "Динара",
    report_recipient_phone: "",
    updated_at: "2026-09-23T09:00:00Z",
    seen_chats: [],
    ...overrides,
  };
}

/** Состояние запущенного бота без сообщений; время последнего опроса задаёт тест. */
export function makeBotStatus(overrides: Partial<WhatsAppBotStatus> = {}): WhatsAppBotStatus {
  return {
    server_enabled: true,
    runtime_status: "running",
    runtime_error: "",
    polled_at: null,
    instance_state: "authorized",
    instance_state_at: null,
    counts: { review: 0, applied: 0, ignored: 0, all: 0 },
    settings: makeBotSettings(),
    ...overrides,
  };
}

/** Нераспознанный отчёт Джин-Сина об отгрузке вагонов, ждёт проверки. */
export function makeBotMessage(overrides: Partial<BotMessage> = {}): BotMessage {
  return {
    id: 7,
    kind: "message",
    status: "needs_review",
    chat_name: "Отгрузка вагонов",
    sender_id: "998901112233@c.us",
    sender_name: "Джин-Син",
    text: "сб 19.09.26 Узбекистан ООО OSIYO NAV NIHOL\nСт. Раустан 12 вагон",
    sent_at: "2026-09-19T09:30:00Z",
    received_at: "2026-09-19T09:30:02Z",
    parsed: {},
    issues: [],
    draft: "",
    order: null,
    original: null,
    reply: "",
    reply_sent_at: null,
    reply_attempts: 0,
    error: "",
    resolved_by_name: "",
    resolved_at: null,
    ...overrides,
  };
}
