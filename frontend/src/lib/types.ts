import type { LineDirection, NormalizedLine, VerificationLine } from "@/lib/camera-counting-line";

export interface Me {
  id: number;
  username: string;
  first_name?: string;
  last_name?: string;
  is_client: boolean;
  is_superuser: boolean;
  permissions: string[];
  position: string | null;
  sales_department: Pick<Department, "id" | "code" | "name" | "color"> | null;
}

/** Код товара в отчётах о вагонах («Д1с»), хранится ключом сравнения. */
export interface ProductAlias {
  id: number;
  code: string;
}

/** Вагон отгрузки по отчёту о вагонах. */
export interface ShipmentWagon {
  number: string;
  product_label: string;
  bags: number;
  weight_kg: string;
}

export interface Product {
  id: number;
  name: string;
  color?: "Red" | "Green" | "Blue";
  color_label?: string;
  weight_kg: string;
  is_active: boolean;
  label: string;
  available_bags?: number;
  /** Подписанная ссылка на фото (см. apiFileUrl); null — фото нет. */
  photo_url?: string | null;
  /** Коды товара в отчётах о вагонах. */
  aliases?: ProductAlias[];
}
/** Товар в каталоге клиента: цена из его личного прайса, остатков нет намеренно. */
export interface PortalProduct {
  id: number;
  label: string;
  weight_kg: string;
  price: string | null;
  currency: "KZT" | "USD";
  photo_url?: string | null;
}
export interface Warehouse {
  id: number;
  code: string;
  name: string;
  address: string;
  is_active: boolean;
  is_default: boolean;
}
interface ClientPriceRow {
  product: number;
  product_label: string;
  currency: "KZT" | "USD";
  price: string | null;
  updated_at: string | null;
  updated_by_name: string | null;
}
export interface ClientPriceSheet {
  client: Pick<Client, "id" | "name">;
  prices: ClientPriceRow[];
}
export interface Department {
  id: number;
  code: string;
  name: string;
  color: string;
  is_active: boolean;
  is_default: boolean;
  order_count: number;
  created_at: string;
  /** Ключ ApiPay задан у отдела; сам ключ API не отдаёт. */
  apipay_configured: boolean;
  apipay_webhook_configured: boolean;
  /** «••••ab12» — последние символы ключа, только суперюзеру. */
  apipay_key_hint?: string;
  apipay_updated_at: string | null;
}
export interface DepartmentSummary {
  id: number;
  code: string;
  name: string;
  color: string;
  is_active: boolean;
  orders: number;
  active: number;
  shipped: number;
  /** Выручка в основной валюте отдела; полная раскладка — revenue_by_currency. */
  revenue: string;
  revenue_currency: "KZT" | "USD";
  revenue_by_currency: Record<string, string>;
  /** Непогашенный остаток отгруженных заказов «в долг». */
  debt: string;
  debt_by_currency: Record<string, string>;
  /** Сколько из выручки уже получено деньгами. */
  paid: string;
  paid_by_currency: Record<string, string>;
  paid_orders: number;
  partial_orders: number;
  unpaid_orders: number;
  debt_orders: number;
}

/**
 * Итоги «Общей» аналитики заказов (`/orders/list-summary/`) по всей выборке
 * списка с теми же фильтрами и поиском. Без отменённых и отклонённых.
 */
export interface OrderListSummary {
  orders: number;
  /** Не отгружены и не закрыты. */
  active: number;
  /** Основная валюта: в ней крупная сумма и доли статусов. */
  total_currency: string;
  total_by_currency: Record<string, string>;
  /** Стоимость заказов по публичным группам статусов, в основной валюте. */
  by_status_group: Record<string, string>;
}

export interface ReportDay {
  date: string;
  orders: number;
  bags: number;
  revenue: string;
  /** Сколько стоимости отгрузок этого дня уже погашено на момент запроса. */
  paid_amount: string;
  /** Текущий непогашенный остаток отгрузок этого дня. */
  debt_amount: string;
  cash: string;
  cashless: string;
  refunded: string;
  received: string;
  payments: number;
  refunds: number;
  revenue_by_currency: Record<string, string>;
  paid_amount_by_currency: Record<string, string>;
  debt_amount_by_currency: Record<string, string>;
  cash_by_currency: Record<string, string>;
  cashless_by_currency: Record<string, string>;
  refunded_by_currency: Record<string, string>;
  received_by_currency: Record<string, string>;
}

export interface ReportClientOrder {
  id: number;
  date: string;
  bags: number;
  total: string;
  currency: string;
  remaining_amount: string;
  payment_status: "unpaid" | "partial" | "settled";
}

export interface ReportClientRow {
  id: number;
  name: string;
  orders: number;
  bags: number;
  revenue_by_currency: Record<string, string>;
  paid_amount_by_currency: Record<string, string>;
  /** Снимок текущего непогашенного остатка отгрузок периода. */
  debt_amount_by_currency: Record<string, string>;
  order_list: ReportClientOrder[];
}

/** Canonical accounting response from GET /reports/summary/. */
export interface DepartmentReport {
  code: string;
  name: string;
  color: string;
  orders: number | null;
  sales_by_currency: Record<string, string> | null;
  received_by_currency: Record<string, string>;
  refunded_by_currency: Record<string, string>;
  net_by_currency: Record<string, string>;
  /** Число подтверждённых оплат отдела. */
  payments: number;
}

export interface ReportSummary {
  departments: DepartmentReport[];
  from: string | null;
  to: string | null;
  income: {
    total: string;
    cash: string;
    cashless: string;
    gross: string;
    refunded: string;
    payments: number;
    refunds: number;
    currency: string;
    by_currency: Record<string, string>;
    cash_by_currency: Record<string, string>;
    cashless_by_currency: Record<string, string>;
    gross_by_currency: Record<string, string>;
    refunded_by_currency: Record<string, string>;
    /** {валюта: {способ: нетто}}. */
    by_method_by_currency: Record<string, Record<string, string>>;
    payments_by_method: Record<string, number>;
    /** Подписи способов из by_method_by_currency. */
    method_labels: Record<string, string>;
  };
  shipped: {
    revenue: string;
    orders: number;
    bags: number;
    /** Погашенная часть выбранных отгрузок на момент запроса. */
    paid_amount: string;
    /** Текущий долг выбранных отгрузок, а не первоначальный способ расчёта. */
    debt_amount: string;
    currency: string;
    revenue_by_currency: Record<string, string>;
    paid_amount_by_currency: Record<string, string>;
    debt_amount_by_currency: Record<string, string>;
  };
  debt_now: {
    total: string;
    by_currency: Record<string, string>;
    currency: string;
    orders: number;
    overdue_by_currency: Record<string, string>;
    overdue_currency: string;
    overdue_clients: number;
  };
  clients: ReportClientRow[];
  days: ReportDay[];
}

export type PortalPaymentMethod = "pending" | "invoice" | "kaspi" | "cash" | "debt";
/** Order.payment_method: выбор клиента по заказу, «mixed» — несколько способов сразу. */
type OrderPaymentMethod = PortalPaymentMethod | "mixed";
/** Способ конкретной оплаты (Payment.method): касса + легаси «card». */
type PaymentMethod = "cash" | "kaspi" | "remote" | "invoice" | "card";

export interface Client {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  phone: string;
  name: string;
  company_name: string;
  country: string;
  currency: "KZT" | "USD";
  iin: string;
  bank: string;
  bank_account: string;
  department: number | null;
  department_name: string | null;
  user: number;
  portal_access_enabled: boolean;
  /** Долг в основной валюте клиента (debt_currency). Валюты не складываются. */
  debt_total?: string;
  debt_currency?: "KZT" | "USD";
  /** Полная раскладка долга по валютам заказов. */
  debt_by_currency?: Record<string, string>;
  created_at?: string;
}
export interface Store {
  id: number;
  client: number;
  client_name?: string;
  name: string;
  address: string;
  phone: string;
  payment_schedule_type: "none" | "monthly" | "weekly";
  payment_days: number[];
}
export interface Notification {
  id: number;
  text: string;
  is_read: boolean;
  created_at: string;
}
interface OrderItem {
  id?: number;
  product: number | null;
  product_label?: string;
  quantity: number;
  unit_price?: string | null;
  client_price?: string | null;
  weight_kg?: string | null;
}
interface StatusChangeRequest {
  id: number;
  order: number;
  to_status: string;
  status: string;
  requested_by_name?: string | null;
  created_at: string;
  decided_at?: string | null;
}
export interface Order {
  rejection_reason?: string;
  client_department?: string;
  client_department_name?: string;
  id: number;
  client: number;
  store?: number | null;
  warehouse: number;
  warehouse_name: string;
  client_name?: string;
  client_phone?: string;
  department?: string;
  department_name?: string;
  department_color?: string;
  currency: "KZT" | "USD";
  status: string;
  payment_status?: string;
  settlement_intent?: string;
  payment_method?: OrderPaymentMethod;
  payment_method_label?: string;
  transport_type: "truck" | "train";
  truck_number: string;
  /** Полуприцеп фуры; у вагона пусто. */
  trailer_number?: string;
  /** Станция назначения вагонного заказа (пишет отгрузка по отчёту). */
  rail_station?: string;
  /** Вагоны отгрузки по отчёту о вагонах. */
  wagons?: ShipmentWagon[];
  arrival_date?: string | null;
  notes?: string;
  items: OrderItem[];
  total_amount: string;
  paid_total: string;
  remaining_amount: string;
  /** Окно оплаты для сотрудника считает сервер (statuses.is_payment_open): до
   * отгрузки — предоплата только деньгами у кассы, после — любым способом. */
  payment_open?: boolean;
  /** Способы, которыми сейчас можно записать оплату: cash/kaspi/remote, после отгрузки и invoice. */
  payment_open_methods?: string[];
  /** Запрос денег (Kaspi QR через ApiPay, счёт на телефон) — только после отгрузки. */
  payment_request_open?: boolean;
  /** Переплата — сколько ещё вернуть клиенту (debt.order_overpaid). */
  overpaid_amount?: string;
  has_pending_payment?: boolean;
  is_fully_paid: boolean;
  is_debt?: boolean;
  debt_requested?: boolean;
  pending_status_requests?: StatusChangeRequest[];
  payments?: Payment[];
  pending_payments?: Payment[];
  weigh_in_kg?: string | null;
  bags_loaded?: number;
  bag_estimate_kg: string;
  created_at: string;
  shipped_at?: string | null;
  loading_camera?: string;
  deleted_at?: string | null;
  deleted_by_name?: string | null;
}

export interface DashboardOperationalSummary {
  queue: Order[];
  attention: {
    pending_payments: number;
    awaiting_review: number;
  };
  days: {
    date: string;
    bags: number;
    orders: number;
  }[];
}

/** Client-portal projection: prices are deliberately hidden until confirmation. */
export interface PortalOrder {
  department?: string;
  department_name?: string;
  rejection_reason?: string;
  id: number;
  status: string;
  payment_status?: string;
  settlement_intent: string;
  payment_method: OrderPaymentMethod;
  currency: "KZT" | "USD";
  transport_type: "truck" | "train";
  store: number | null;
  store_name: string | null;
  items: OrderItem[];
  total_amount: string | null;
  paid_total: string | null;
  remaining_amount: string | null;
  has_pending_payment: boolean;
  available_amount: string | null;
  payment_parts: {
    id: number;
    amount: string;
    method: "invoice" | "kaspi" | "cash" | "remote";
    method_label: string;
    status: PaymentStage;
    can_release: boolean;
    apipay_invoice: ApiPayInvoiceView | null;
  }[];
  client_phone: string;
  /** Страна клиента — страна номера по умолчанию. */
  client_country?: string;
  receipt_available: boolean;
  /** Пусто, когда машина уже на территории: после заезда номер клиенту не показывается. */
  truck_number: string;
  trailer_number?: string;
  rail_station?: string;
  /** Вагоны отгрузки по отчёту — только для чтения. */
  wagons?: ShipmentWagon[];
  /** Номер указал менеджер или машина уже заехала: только для чтения. */
  transport_locked?: boolean;
  debt_requested: boolean;
  created_at: string;
}
type PaymentStage = "requested" | "received" | "confirmed" | "rejected";

/** Счёт ApiPay в ответе API (`apipay_invoice_data` на бэке) — один вид для кассы и кабинета клиента. */
export interface ApiPayInvoiceView {
  invoice_id: number | null;
  status: string;
  channel: "phone" | "qr";
  phone_number: string | null;
  qr_token_url: string | null;
  qr_image_url: string | null;
  qr_expires_at: string | null;
}

export interface Payment {
  id: number;
  order: number;
  currency?: "KZT" | "USD";
  amount: string;
  method: PaymentMethod;
  /** Подписи способа и статуса — из labels.py на бэке, своих словарей фронт не держит. */
  method_label: string;
  note?: string;
  status: PaymentStage;
  status_label: string;
  paid_at: string;
  recorded_by_name?: string | null;
  received_by_name?: string | null;
  received_at?: string | null;
  confirmed_by_name?: string | null;
  confirmed_at?: string | null;
  effective_status?: string;
  /** Подпись effective_status: этап кассы или состояние счёта провайдера. */
  effective_status_label: string;
  refunded_amount?: string;
  pending_refund_amount?: string;
  available_for_refund?: string;
  can_restore?: boolean;
  /** Ошибочно подтверждённую оплату кассы можно вернуть на проверку. */
  can_reopen?: boolean;
  can_issue?: boolean;
  confirmation_mode?: "manual" | "automatic";
  refunds?: {
    id: number;
    amount: string;
    method: "apipay" | "apipay_qr" | "cash";
    status: "pending" | "completed" | "failed";
    reason: string;
    requested_by_name: string | null;
    completed_at: string | null;
    created_at: string;
  }[];
  client_name?: string;
  provider?: ApiPayInvoiceView | null;
}

export interface PaymentQueueItem extends Payment {
  client_name: string;
  department: string;
  department_name?: string;
  department_color?: string;
  store_name?: string | null;
}
export interface StockItem {
  id: number;
  warehouse: number;
  warehouse_name: string;
  product: number;
  product_label: string;
  grade: string;
  color: string;
  color_label: string;
  packaging: string;
  weight_kg: string;
  bags: number;
}
/** Строка агрегата GET /clients/debts/. */
export interface ClientDebt {
  client_id: number;
  client_name: string;
  client_phone: string;
  /** Долг в основной валюте клиента. Полная разбивка — в debt_by_currency. */
  debt_total: string;
  debt_currency: "KZT" | "USD";
  debt_by_currency: Record<string, string>;
  orders_count: number;
  unpaid_count: number;
  partial_count: number;
  stores_count: number;
  overdue_count: number;
}
/** Строка продаж GET /clients/{id}/history/. */
export interface ClientHistorySale {
  id: number;
  date: string;
  status: string;
  /** Входит в «Сумму продаж» (правило бэка): заявки, отказы и отмены — нет. */
  is_financial: boolean;
  settlement_intent: string;
  items: { label: string; qty: number }[];
  bags: number;
  amount: string;
  paid: string;
  currency: string;
}
/** Платёж клиента из /clients/{id}/history/ — вся история, включая погашенные заказы. */
export interface ClientHistoryPayment {
  id: number;
  order_id: number;
  date: string;
  employee: string | null;
  method: string;
  method_label: string;
  status: string;
  status_label: string;
  amount: string;
  /** Сколько платёж даёт в «Оплачено»: нетто подтверждённой оплаты, иначе 0. */
  counted_amount: string;
  currency: string;
  can_reopen: boolean;
  can_reject: boolean;
  provider: boolean;
  refunded_amount: string;
}
export interface ClientHistoryDebt {
  id: number;
  date: string;
  bags: number;
  amount: string;
  paid: string;
  remaining: string;
  currency: string;
}
/** Итоги клиента за всё время: выручка и оплачено — по финансовым заказам, долг — остаток отгруженных. */
export type ClientSummaryMoney = { revenue: string; paid: string; debt: string };
/** Ответ GET /clients/{id}/history/ — карточка клиента. */
export interface ClientHistory {
  client: { id: number; name: string; phone: string; country: string };
  summary: ClientSummaryMoney & {
    currency: string;
    by_currency: Record<string, ClientSummaryMoney>;
    orders_count: number;
  };
  sales: ClientHistorySale[];
  payments: ClientHistoryPayment[];
  debts: ClientHistoryDebt[];
}
export interface AiCountingSnapshot {
  total?: number;
  weight?: number;
  status?: string;
  per_color?: Record<string, number>;
}

export interface AiCountingSession {
  id: number;
  order_id: number;
  order_client_name: string;
  order_transport_type: "truck" | "train";
  camera: string;
  status: "starting" | "active";
  started_at: string;
  started_by_name: string;
  automatically_started?: boolean;
  last_status: AiCountingSnapshot;
}
export interface ShippingBoardSettings {
  completed_orders_days: number;
  video_retention_days: number;
  updated_at: string | null;
}
/** Линия подсчёта камеры в ответе AI-сервиса (GET/PUT /cameras/{src}/counting-line). */
export interface CameraCountingLine {
  configured: boolean;
  coordinate_space: "normalized";
  line: NormalizedLine | null;
  line_spec?: string | null;
  direction: LineDirection;
  updated_at?: string | null;
  /** Линии проверки мешков: классифицируют, но не считают. */
  verification_lines?: VerificationLine[] | null;
  /** false — AI-сервис старый и не хранит линии проверки. */
  verification_lines_supported?: boolean;
  /** Только в ответе на «Обновить статус»: работает ли камера с этими линиями. */
  line_applied?: "applied" | "not_applied" | "not_running";
}
/** Камера из живого инвентаря сети (бэкенд строит его из ai_service). */
export interface CameraFeed {
  /** Стабильный ключ: kind + MAC (не меняется при перетасовке каналов NVR). */
  id: string;
  name: string;
  zone: string;
  /** Имя потока в go2rtc (cam2, cam_8c26); null у locked-камер. */
  src: string | null;
  kind: "nvr-channel" | "direct" | "locked";
  /** Живость источника по данным инвентаря (у locked всегда false). */
  online: boolean;
  /** Пояснение для locked: обнаружена, но пароль неизвестен. */
  note?: string;
  /** Сохранённая AI-сервисом линия подсчёта, если камера её поддерживает. */
  line_config?: CameraCountingLine | null;
}
export interface MonoblockCameraSettings {
  camera_sources: string[];
  /** Cameras explicitly owned by the separate AI 24/7 contour. */
  blocked_camera_sources?: string[];
  updated_at: string | null;
}
/**
 * Рамка мешка на последнем кадре.
 *
 * Поля необязательные: ответ приходит с ПК цеха как есть, поэтому
 * `normalizeDetections` отбрасывает неполные записи, а не падает на них.
 */
export interface AlwaysOnDetection {
  /** Пиксели кадра модели `[x1, y1, x2, y2]`; масштаб — `detection_frame`. */
  bbox?: [number, number, number, number];
  class_name?: string;
  confidence?: number;
}

export interface AlwaysOnProcessorStatus {
  cam: string;
  running: boolean;
  /** Process still owns a live capture/inference worker on the camera PC. */
  processor_alive?: boolean;
  mode: "always_on" | "session" | "idle";
  analytics_scope?: "shipping" | "ai_247";
  source?: "main" | "sub";
  recording: boolean;
  total: number;
  /** Confident (>= count threshold) bags on the latest AI frame. */
  bags_present?: boolean | null;
  detections?: AlwaysOnDetection[];
  /** Размер кадра модели — по нему пиксельные рамки переводятся в доли. */
  detection_frame?: { width?: number; height?: number } | null;
  /** Фактически применённая процессором линия подсчёта. */
  line?: string | NormalizedLine | null;
  direction?: LineDirection;
  per_color?: Record<string, number>;
  last_frame_at?: string | null;
  error?: string | null;
  metrics?: { inference_fps?: number; dropped_frames?: number };
}
export interface CameraCountingLine {
  configured: boolean;
  coordinate_space: "normalized";
  line: NormalizedLine | null;
  line_spec?: string | null;
  direction: LineDirection;
  updated_at?: string | null;
  /** Линии проверки мешков: классифицируют, но не считают. */
  verification_lines?: VerificationLine[] | null;
  /** false — AI-сервис старый и не хранит линии проверки. */
  verification_lines_supported?: boolean;
  /** Только в ответе на «Обновить статус»: работает ли камера с этими линиями. */
  line_applied?: "applied" | "not_applied" | "not_running";
}
/** Камера из живого инвентаря сети (бэкенд строит его из ai_service). */
export interface CameraFeed {
  /** Стабильный ключ: kind + MAC (не меняется при перетасовке каналов NVR). */
  id: string;
  name: string;
  zone: string;
  /** Имя потока в go2rtc (cam2, cam_8c26); null у locked-камер. */
  src: string | null;
  kind: "nvr-channel" | "direct" | "locked";
  /** Живость источника по данным инвентаря (у locked всегда false). */
  online: boolean;
  /** Пояснение для locked: обнаружена, но пароль неизвестен. */
  note?: string;
  /** Сохранённая AI-сервисом линия подсчёта, если камера её поддерживает. */
  line_config?: CameraCountingLine | null;
}
export interface CameraContinuousReadiness {
  status: "synced" | "pending";
  detail: string;
}
export interface AnalyticsSyncState {
  status: "synced" | "pending" | "catching_up" | "error" | "stale" | "unsupported";
  available: boolean;
  caught_up_at?: string | null;
  last_event_at?: string | null;
  error?: string;
  detail: string;
}
export interface AlwaysOnCameraSettings {
  camera_sources: string[];
  analytics_scope: "shipping" | "ai_247";
  /** Cameras owned by the other contour and unavailable in this picker. */
  blocked_camera_sources?: string[];
  /** Currently active cameras in the other contour; used only for the shared runtime capacity. */
  active_other_camera_sources: string[];
  source: "sub" | "main";
  processors: AlwaysOnProcessorStatus[];
  capacity: number | null;
  service_available: boolean;
  sync_status: "synced" | "pending";
  detail: string;
  camera_readiness?: Record<string, CameraContinuousReadiness>;
  updated_at: string | null;
}
export type TransportRecognitionModel = "vehicle_number" | "wagon_number";
export type ShippingLoadingZone = [number, number, number, number];

export interface ShippingTransportCameraSettings {
  conveyor_camera: string;
  number_camera: string | null;
  recognition_model: TransportRecognitionModel | null;
  loading_zone?: ShippingLoadingZone | null;
  updated_at: string | null;
}

export interface ShippingTransportRecognition {
  conveyor_camera: string;
  number_camera: string;
  recognition_model: TransportRecognitionModel;
  number: string | null;
  observed_at: string;
}
/** Камера номеров вагонов: настройка CRM, действует сразу после сохранения. */
export interface WagonNumberCameraSettings {
  camera_source: string | null;
  source: "main";
  updated_at: string | null;
}
export interface WagonArchMotion {
  state: "moving" | "still" | "unknown";
  still_seconds: number;
  direction: string;
  status: string;
  sample_age_seconds: number | null;
}
interface WagonArchCollector {
  total: number;
  pending: number;
  status: string;
  standing: string | null;
  motion: string | null;
  heartbeat_at: number | null;
}
export type WagonArchStopStatus = "open" | "closed" | "attention" | "superseded";
interface WagonArchLastStop {
  id: number;
  stop_id: string;
  number: string;
  status: WagonArchStopStatus;
  full_weight_kg: number;
  exit_weight_kg: number | null;
  arrived_at: string;
  wagon_id: number | null;
  blocked_reason: string;
  blocked_detail: string;
}
export interface WagonArchRuntime {
  enabled: boolean;
  camera: string;
  collector: WagonArchCollector | null;
  pending_stops: number;
  attention_stops: number;
  last_stop: WagonArchLastStop | null;
  updated_at: string | null;
}
/** Зона камеры (ROI) — с ПК цеха, его обновляют отдельно от CRM. */
export type VehicleRoiConfig = {
  configured: boolean;
  enabled: boolean;
  source: string;
  coordinate_space: string;
  points: unknown;
  updated_at?: string | null;
};
export interface WagonArchCameraRuntime {
  camera: string;
  source: "main";
  stream: string;
  automation_enabled: boolean;
  zone: VehicleRoiConfig;
  motion: WagonArchMotion | null;
  runtime: WagonArchRuntime;
  diagnostic: string;
}
export interface VehiclePlateMonitor {
  status: string;
  scanned_frames: number;
  plate_detections: number;
  stationary_admissions: number;
  ocr_attempts: number;
  confirmed_events: number;
  has_error: boolean;
}
export interface ScaleAutomationRuntime {
  enabled: boolean;
  stable_weight_seconds: number;
  state:
    | "disabled"
    | "idle"
    | "candidate"
    | "recognizing"
    | "applying"
    | "awaiting_clear"
    | "manual_required"
    | "unavailable";
  last_checked_at: string | null;
  heartbeat_stale: boolean;
  active: {
    request_id: string;
    stage: "claimed" | "recognizing" | "applying" | "done";
    action: "entry" | "exit" | null;
    wagon_id: number | null;
    retryable: boolean;
    error_code: string | null;
  } | null;
}
export interface VehiclePlateRuntime {
  camera: string;
  enabled: boolean;
  ready: boolean;
  automation_enabled: boolean;
  camera_configured: boolean;
  weight_first_enabled: boolean;
  on_demand_enabled: boolean;
  on_demand_camera_configured: boolean;
  source: "main" | "sub";
  stream: string;
  server_push_configured: boolean;
  monitor: VehiclePlateMonitor | null;
  roi: VehicleRoiConfig;
}
export interface WagonArchStop {
  id: number;
  stop_id: string;
  camera: string;
  arrived_at: string;
  full_weight_kg: number;
  exit_weight_kg: number | null;
  net_kg: number | null;
  number: string;
  number_source: string;
  recognition_error: string;
  ocr_attempts: number;
  status: WagonArchStopStatus;
  blocked_reason: string;
  blocked_detail: string;
  motion_gap: boolean;
  departed_at: string | null;
  entry_applied_at: string | null;
  exit_applied_at: string | null;
  wagon_id: number | null;
  wagon_status: string;
  continues: number | null;
  photo_url: string | null;
}
/**
 * Как CRM определила цвет/бренд мешков, которые камера оставила «unknown»:
 * по соседним мешкам, по голосам отдельных кадров проверки или вручную.
 */
export type AlwaysOnInferredMethod = "neighbors" | "votes" | "manual";
/** Сколько мешков определено каждым способом; нет поля — всё распознала камера. */
export type AlwaysOnInferred = Partial<Record<AlwaysOnInferredMethod, number>>;
export interface AlwaysOnColorAnalytics {
  color: string;
  total: number;
  percent: number;
  inferred?: AlwaysOnInferred;
}
export interface AlwaysOnHistoryPoint {
  day: string;
  model_total: number;
  model_per_color: Record<string, number>;
  /** Готовая разбивка по цветам за этот день — считает бэкенд. */
  colors: AlwaysOnColorAnalytics[];
  adjustment: number;
  total: number;
  updated_at: string | null;
}
export interface AlwaysOnDailyCameraAnalytics {
  /** Inclusive calendar range, present when date_from/date_to were requested. */
  date_from?: string;
  date_to?: string;
  period_total?: number;
  camera: string;
  day: string;
  model_total: number;
  model_per_color: Record<string, number>;
  adjustment: number;
  total: number;
  all_time_total: number;
  history: AlwaysOnHistoryPoint[];
  colors: AlwaysOnColorAnalytics[];
  updated_at: string | null;
  analytics_sync?: AnalyticsSyncState;
}
export interface AlwaysOnDailyAnalytics {
  analytics_scope?: "shipping" | "ai_247";
  analytics_sync?: AnalyticsSyncState;
  day: string;
  total: number;
  all_time_total: number;
  cameras: AlwaysOnDailyCameraAnalytics[];
}
export interface AlwaysOnProductionRun {
  id: number;
  camera: string;
  business_day: string;
  color: string;
  started_at: string;
  last_counted_at: string;
  ended_at: string | null;
  model_bags: number;
  is_approximate: boolean;
  status: "active" | "closed";
  /** Only present for `day_runs`: this interval overlaps a calendar-day edge. */
  starts_before_day?: boolean;
  ends_after_day?: boolean;
  is_partial_for_day?: boolean;
  /** Цвет мешков этого отрезка определила CRM, а не камера. */
  inferred?: AlwaysOnInferred;
  /** Что сказала камера (обычно `unknown`), когда цвет определён CRM. */
  source_color?: string;
  /** Номер части периода `unknown`, разбитого по определённым цветам (тот же `id`). */
  segment?: number;
}
interface AlwaysOnRunSmoothing {
  n_min: number;
  raw_model_total: number;
  algorithm_model_total: number;
  raw_colors: AlwaysOnColorAnalytics[];
  algorithm_colors: AlwaysOnColorAnalytics[];
}
export interface AlwaysOnProductMapping {
  color: string;
  product: number | null;
  product_label: string | null;
}
export interface AlwaysOnStockPosting {
  id: number;
  /** `shift` — приход смены в 19:00, `manual_color` — цвет указан вручную позже. */
  kind?: "shift" | "manual_color";
  color: string;
  product: number;
  product_label: string;
  detected_bags: number;
  /** Мешки без цвета от камеры, которым CRM определила этот цвет. */
  resolved_bags?: number;
  correction_bags: number;
  posted_bags: number;
  receipt_id: number;
}
export interface AlwaysOnStockBatch {
  id: number;
  camera: string;
  warehouse: number;
  warehouse_name: string;
  business_day: string;
  scheduled_for: string;
  status: "scheduled" | "blocked" | "posted" | "empty" | "failed";
  total_bags: number;
  /** Мешки смены без цвета: не оприходованы, ждут «Указать цвет». */
  pending_bags: number;
  last_error: string;
  attempts: number;
  items: AlwaysOnStockPosting[];
}
export interface AlwaysOnProductionProduct {
  id: number;
  label: string;
  color: string;
  color_label: string;
  weight_kg: string;
  /** Every warehouse with a stock card. */
  warehouse_ids: number[];
}
export interface AlwaysOnStockPreview {
  color: string;
  detected_bags: number;
  /** Мешки без цвета от камеры, которым CRM определила этот цвет. */
  resolved_bags?: number;
  inferred?: AlwaysOnInferred;
  correction_bags: number;
  net_bags: number;
  product: number | null;
  product_label: string | null;
  configured: boolean;
}
/** Read-only day detail shared by shipment and production cameras. */
interface CameraDayHistory {
  camera: string;
  timezone: string;
  selected_day: string | null;
  day_runs: AlwaysOnProductionRun[];
  algorithm_day_runs: AlwaysOnProductionRun[];
  run_smoothing: AlwaysOnRunSmoothing;
  dominant_brand_by_color?: Record<string, string | null>;
}
export interface ShippingCameraDayHistory extends CameraDayHistory {
  selected_day: string;
  history_status: "complete" | "incomplete" | "pending";
  history_detail: string;
}
export interface AlwaysOnProductionPayload extends CameraDayHistory {
  warehouse: number;
  warehouse_name: string;
  warehouses: Array<Omit<Warehouse, "address"> & { address?: string }>;
  close_time: string;
  current_business_day: string;
  next_run_at: string;
  fully_configured: boolean;
  available_colors: string[];
  mappings: AlwaysOnProductMapping[];
  products: AlwaysOnProductionProduct[];
  preview: AlwaysOnStockPreview[];
  /** Мешки текущей смены без цвета — не блокируют приход, ждут «Указать цвет». */
  unresolved: { business_day: string; bags: number };
  batches: AlwaysOnStockBatch[];
}
/** POST /cameras/always-on-production/unknown-colors/ — «Указать цвет». */
export interface AlwaysOnUnknownColorInput {
  business_day: string;
  color: string;
  bags: number;
  reason: string;
}
export interface Permission {
  id: number;
  code: string;
  section: string;
  action: string;
  label: string;
  /** Подпись раздела = страница меню (с бэкенда). */
  section_label: string;
}
export interface Employee {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  phone: string;
  position: string;
  sales_department: number | null;
  sales_department_name: string | null;
  sales_department_color: string | null;
  name: string;
  permissions: string[];
  is_active: boolean;
}
export interface EventLog {
  id: number;
  event_type: string;
  message: string;
  user: number | null;
  user_name: string | null;
  order: number | null;
  payload: Record<string, unknown>;
  created_at: string;
}

type TaskStatus = "pending" | "done";

interface TaskAttachment {
  id: number;
  kind: "photo" | "voice";
  url: string | null;
  original_name: string;
  size_bytes: number;
  created_at: string;
}

export interface Task {
  id: number;
  title: string;
  body: string;
  status: TaskStatus;
  status_label: string;
  assignee: number;
  assignee_name: string | null;
  created_by: number | null;
  created_by_name: string | null;
  due_date: string | null;
  done_at: string | null;
  done_by_name: string | null;
  attachments: TaskAttachment[];
  /** Закрыть может исполнитель, постановщик или суперадмин — решает бэкенд. */
  can_complete: boolean;
  /** Удалить может только постановщик или суперадмин — решает бэкенд. */
  can_delete: boolean;
  created_at: string;
  updated_at: string;
}

export interface TaskAssignee {
  id: number;
  name: string;
  position: string;
}

/* ── Приход зерна ─────────────────────────────────────────────────────── */

export interface TruckScalePreview {
  state: "ready" | "unstable" | "stale" | "disconnected" | "unavailable" | "disabled" | "malformed" | "refreshing";
  enabled: boolean;
  ready: boolean;
  capturable: boolean;
  connected: boolean;
  stable: boolean;
  stale: boolean;
  weight_kg: string | null;
  age_seconds: string | null;
  updated_at: string | null;
  observed_at: string;
}

export interface GrainWeighing {
  photo_status?: "pending" | "retrying" | "saved" | "unavailable";
  id: number;
  kind: "gross" | "tare";
  weight_kg: number;
  scale_number: string;
  source: "auto" | "manual" | "scale" | "historical";
  reference_record?: number | null;
  reference_record_at?: string | null;
  reference_record_source?: "auto" | "manual" | "scale" | "historical" | null;
  manual_reason: string;
  previous_weight_kg: number | null;
  operator_name: string | null;
  /** Подписанная ссылка на кадр машины с Camera-PC; null — фото нет. */
  photo_url?: string | null;
  /** Как машина стояла на кадре: front — передом к камере (заезд), rear — задом (выезд). */
  orientation?: VehicleOrientation;
  created_at: string;
}

/** Ориентация машины по камере весовой; пустая строка — не определена. */
export type VehicleOrientation = "" | "front" | "rear";

/** Сохранённое взвешивание в автоматической обработке либо на резервной ручной проверке. */
export interface GrainUnassignedWeighing {
  identity_check?: {
    status:
      "pending" | "processing" | "retrying" | "review" | "matched" | "disabled" | "waiting_photo" | "waiting_budget";
    reason: string;
    plate: string;
  };
  photo_status?: "pending" | "retrying" | "saved" | "unavailable";
  id: number;
  weight_kg: number;
  stable_weight_at: string;
  scale_number: string;
  camera: string;
  photo_url: string | null;
  /** Почему вес не привязался к рейсу: plate_unreadable, entry_missing и т. п. (подписи — weighingReasonLabel). */
  reason: string;
  /** Номер, прочитанный камерой или подтверждённый автоматической проверкой. */
  vehicle_number: string;
  orientation: VehicleOrientation;
  status: "open" | "assigned" | "discarded";
  wagon: number | null;
  wagon_number: string;
  action: "" | "entry" | "exit";
  resolved_by_name: string | null;
  resolved_at: string | null;
  created_at: string;
}

/** Метка кадра в датасете ориентации: передом к камере — заезд, задом — выезд. */
export type GrainOrientationLabel = Exclude<VehicleOrientation, "">;

/** Кадр весовой, который CRM собирает для дообучения классификатора ориентации на Camera-PC. */
export interface GrainOrientationSample {
  id: number;
  /** `${record_kind}-${record_id}` — ключ кадра на Camera-PC. */
  sample_id: string;
  record_kind: "weighing" | "unassigned";
  record_id: number;
  label: GrainOrientationLabel;
  /** trip — по завершённому рейсу, weight — по весу, manual — поправил человек. */
  label_source: "trip" | "weight" | "manual";
  weight_kg: number;
  captured_at: string;
  /** Что сказал классификатор в момент кадра; пусто — не отвечал. */
  model_orientation: VehicleOrientation;
  /** Классификатор уверенно противоречил автоматической метке: кадр придержан до проверки. */
  conflict: boolean;
  /** Человек убрал кадр из датасета. */
  excluded: boolean;
  /** Когда Camera-PC принял кадр; null — ещё не отправлен. */
  sent_at: string | null;
  last_error: string;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  /** Подписанная ссылка на кадр (см. apiFileUrl); null — фото нет. */
  photo_url: string | null;
  vehicle_number: string;
  wagon: number | null;
}

/** Отчёт ночного обучения на Camera-PC; статусы задаёт ПК, поэтому строка. */
export interface GrainOrientationTrainingReport {
  status: string;
  ran_at: string | null;
  promoted: boolean;
  /** Кадров по классам на момент запуска; нет, пока обучение не запускалось (ПК отдаёт `{}`). */
  samples?: { front: number; rear: number };
  baseline?: { accuracy: number | null } | null;
  candidate?: { accuracy: number | null } | null;
  reason: string;
  current_model: string | null;
}

export interface GrainOrientationCameraPc {
  enabled: boolean;
  /** Описание активной модели в формате ПК; `self_trained` — загружен ли дообученный файл. */
  model?: ({ self_trained?: boolean } & Record<string, unknown>) | null;
  dataset?: { front: number; rear: number } | null;
  training?: GrainOrientationTrainingReport | null;
}

export interface GrainOrientationSummary {
  total: number;
  by_label: { front: number; rear: number };
  by_source: { trip: number; weight: number; manual: number };
  conflicts: number;
  excluded: number;
  unsent: number;
  /** null — ПК камер не ответил. */
  camera_pc: GrainOrientationCameraPc | null;
}

/** Ответ POST /grain/orientation-samples/purge/ — один пакет; клиент повторяет запрос, пока есть `remaining`. */
export interface GrainOrientationPurgeResult {
  deleted: number;
  removed_from_pc: number;
  /** ПК камер не ответил: отправленные ему кадры остались в CRM исключёнными до ночного экспорта. */
  pc_unavailable: boolean;
  /** Сколько кадров под тот же фильтр ещё осталось после этого пакета. */
  remaining: number;
}

export interface GrainAllocation {
  id: number;
  silo: number;
  silo_name: string;
  amount_kg: number;
  measurement_source: string;
  created_at: string;
}

/** Candidate from the gate camera, explicitly selected before creating a passage. */
export interface VehiclePlateCandidate {
  /** Stable external UUID; pass it as `vehicle_plate_event_id` when creating passage. */
  event_id: string;
  vehicle_number: string;
  camera: string;
  source: "main" | "sub";
  detected_at: string;
  stationary_seconds: number | string;
  ocr_confidence: number | string;
}

export interface PassageWeightCapture {
  request_id: string;
  action: "entry" | "exit";
  status: "processing" | "completed" | "failed";
  stage: "claimed" | "recognizing" | "applying" | "done";
  camera: string;
  camera_source: "main" | "sub" | "";
  stable_weight_at: string | null;
  weight_kg: number | null;
  vehicle_number: string;
  recognized_at: string | null;
  confirmation_votes: number | null;
  detector_confidence: string | null;
  ocr_confidence: string | null;
  response_status: number | null;
  retryable: boolean;
  error_code: string;
  error_detail: string;
  started_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface GrainWagon {
  id: number;
  supply: number | null;
  number: string;
  number_source: "camera" | "manual";
  number_camera_source?: string;
  workflow: "simple" | "legacy";
  /** intake — привозят зерно в силос; passage — забирают отруби и увозят. */
  direction: "intake" | "passage";
  /** Что вывозят на проходе. У прихода пустое. */
  cargo_name: string;
  status: string;
  status_label: string;
  unplanned: boolean;
  supplier: string;
  culture: string;
  grain_class: string;
  grain_type: number | null;
  grain_type_name: string;
  document_weight_kg: number | null;
  expected_weight_kg: number | null;
  arrived_at: string | null;
  gross_weight_kg: number | null;
  tare_weight_kg: number | null;
  net_weight_kg: number | null;
  /** Весы по направлению: entry — на въезде, exit — на выезде. */
  entry_weight_kg: number | null;
  exit_weight_kg: number | null;
  weight_difference_kg: number | null;
  weight_difference_percent: number | null;
  weight_matches: boolean | null;
  assigned_silo: number | null;
  assigned_silo_name: string | null;
  unloading_point?: string;
  unloading_started_at?: string | null;
  silo_arrived_at: string | null;
  unloading_finished_at?: string | null;
  exited_at: string | null;
  note?: string;
  created_at: string;
  weighings?: GrainWeighing[];
  allocations?: GrainAllocation[];
  vehicle_recognition_captures?: PassageWeightCapture[];
  /** Фото машины на въезде/выезде (последнее взвешивание с кадром). */
  entry_photo_url?: string | null;
  exit_photo_url?: string | null;
}

export interface GrainSupply {
  id: number;
  supplier: string;
  grain_type: number | null;
  grain_type_name: string;
  grain_type_color: string | null;
  assigned_silo: number | null;
  assigned_silo_name: string | null;
  culture: string;
  grain_class: string;
  expected_total_kg: number | null;
  note: string;
  status: "draft" | "expected" | "closed" | "cancelled";
  created_at: string;
  wagons: GrainWagon[];
}

export interface GrainSilo {
  id: number;
  name: string;
  total_capacity_kg: number;
  silo_type: number | null;
  silo_type_name: string | null;
  silo_type_color: string | null;
  is_default_route: boolean;
  grain_culture: string;
  grain_class: string;
  allow_mixing: boolean;
  is_quarantine: boolean;
  status: "active" | "blocked" | "maintenance";
  unloading_line: string;
  current_balance_kg: number;
  reserved_kg: number;
  free_capacity_kg: number;
  fill_percent: number;
  active_wagons: { id: number; number: string; status: string }[];
}

export interface GrainSiloType {
  id: number;
  name: string;
  grain_culture: string;
  grain_class: string;
  color: string;
  description: string;
  default_silo: number | null;
  default_silo_name: string | null;
  silo_count: number;
  created_at: string;
}

export interface GrainMovement {
  id: number;
  silo: number;
  silo_name: string;
  movement_type: string;
  delta_kg: number;
  balance_after_kg: number;
  wagon: number | null;
  wagon_number: string | null;
  batch_number: string;
  note: string;
  created_by_name: string | null;
  created_at: string;
}

export interface GrainTimelineEvent {
  id: number;
  event_type: string;
  message: string;
  user_name: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

/** Возврат по Kaspi QR через ссылку покупателю (GET /payment-transactions/{id}/qr-refund/). */
export interface QrRefundState {
  id: number;
  status:
    | "issuing"
    | "awaiting_customer"
    | "activating"
    | "awaiting_scan"
    | "customer_identified"
    | "executing"
    | "completed"
    | "execution_uncertain"
    | "expired"
    | "failed";
  /** Сессия ещё может дойти до денег (ApiPayQrRefund.ACTIVE_STATUSES): ждём покупателя или исход возврата. */
  active: boolean;
  amount: string;
  refunded_amount: string | null;
  client_name: string | null;
  /** Ссылка для покупателя — только пока он её не открыл. */
  customer_url: string | null;
  link_expires_at: string | null;
  operations: { ref: string; amount: string; date: string | null; returnable: string; client_name: string | null }[];
  receipt_url: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
}
