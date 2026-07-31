export interface Me {
  id: number;
  username: string;
  is_client: boolean;
  is_superuser: boolean;
  is_monoblock: boolean;
  monoblock_name: string | null;
  monoblock_camera: string | null;
  permissions: string[];
  role_name: string | null;
  client_id: number | null;
  sales_department: Pick<Department, "id" | "code" | "name" | "color"> | null;
}

export type FactoryZoneKind = "gate" | "scale" | "warehouse" | "silos" | "production" | "lab" | "rail" | "utility";

export interface FactoryZone {
  id: string;
  name: string;
  kind: FactoryZoneKind;
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
  note: string;
}

export interface FactoryMap {
  title: string;
  zones: FactoryZone[];
  updated_at: string | null;
  updated_by_name: string | null;
}

export interface Product {
  id: number;
  name: string;
  color?: "Red" | "Green" | "Blue";
  color_label?: string;
  weight_kg: string;
  is_active: boolean;
  label: string;
  cv_class?: string;
  available_bags?: number;
  ask_truck_weight?: boolean;
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
  revenue_currency?: "KZT" | "USD";
  revenue_by_currency?: Record<string, string>;
}

export interface ReportDay {
  date: string;
  orders: number;
  bags: number;
  revenue: string;
  debt_amount: string;
  cash: string;
  cashless: string;
  received: string;
  payments: number;
  revenue_by_currency: Record<string, string>;
  debt_amount_by_currency: Record<string, string>;
  cash_by_currency: Record<string, string>;
  cashless_by_currency: Record<string, string>;
  received_by_currency: Record<string, string>;
}

export interface ReportClientOrder {
  id: number;
  date: string;
  bags: number;
  total: string;
  currency: string;
  on_debt: boolean;
}

export interface ReportClientRow {
  id: number;
  name: string;
  orders: number;
  bags: number;
  revenue_by_currency: Record<string, string>;
  debt_amount_by_currency: Record<string, string>;
  order_list: ReportClientOrder[];
}

/** Canonical accounting response from GET /reports/summary/. */
export interface ReportSummary {
  from: string | null;
  to: string | null;
  income: {
    total: string;
    cash: string;
    cashless: string;
    payments: number;
    currency: string;
    by_currency: Record<string, string>;
    cash_by_currency: Record<string, string>;
    cashless_by_currency: Record<string, string>;
  };
  shipped: {
    revenue: string;
    orders: number;
    bags: number;
    debt_amount: string;
    currency: string;
    revenue_by_currency: Record<string, string>;
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
  /** Может отсутствовать в ответе старого бэкенда во время раскатки. */
  clients?: ReportClientRow[];
  days: ReportDay[];
}

export type PortalPaymentMethod = "pending" | "invoice" | "kaspi" | "cash" | "debt";
type PaymentMethod = PortalPaymentMethod | "card";

export interface Client {
  id: number;
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
  user: number | null;
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
  contract_signed_at: string | null;
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
  cv_class?: string;
  quantity: number;
  price?: string | null;
  unit_price?: string | null;
  client_price?: string | null;
  weight_kg?: string | null;
  ask_truck_weight?: boolean;
}
interface StatusChangeRequest {
  id: number;
  order: number;
  to_status: string;
  to_status_label?: string;
  status: string;
  requested_by?: number | null;
  requested_by_name?: string | null;
  decided_by?: number | null;
  created_at: string;
  decided_at?: string | null;
}
export interface Order {
  id: number;
  client: number;
  store?: number | null;
  client_name?: string;
  client_phone?: string;
  department?: string;
  department_name?: string;
  department_color?: string;
  currency: "KZT" | "USD";
  status: string;
  payment_status?: string;
  settlement_intent?: string;
  payment_method?: PaymentMethod;
  transport_type?: "truck" | "train";
  truck_number: string;
  truck_number_set_by?: number | null;
  arrival_date?: string | null;
  notes?: string;
  items: OrderItem[];
  total_amount: string;
  paid_total: string;
  remaining_amount?: string;
  has_pending_payment?: boolean;
  is_fully_paid: boolean;
  is_debt?: boolean;
  debt_override: boolean;
  debt_requested?: boolean;
  pending_status_requests?: StatusChangeRequest[];
  payments?: Payment[];
  pending_payments?: Payment[];
  weigh_in_kg?: string | null;
  bags_loaded?: number;
  bag_estimate_kg?: string;
  bag_weight_kg?: string;
  debt_override_by_name?: string | null;
  created_at: string;
  shipped_at?: string | null;
  loading_camera?: string;
  repeated_from?: number | null;
  deleted_at?: string | null;
  deleted_by_name?: string | null;
}

export interface DashboardOperationalSummary {
  queue: Order[];
  attention: {
    pending_payments: number;
    awaiting_review: number;
    stuck_in_loading: number;
  };
  days: {
    date: string;
    bags: number;
    orders: number;
  }[];
}

/** Client-portal projection: prices are deliberately hidden until confirmation. */
export interface PortalOrder {
  id: number;
  status: string;
  payment_status?: string;
  settlement_intent: string;
  payment_method: PortalPaymentMethod | "mixed";
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
    method: "invoice" | "kaspi" | "cash";
    status: PaymentStage;
    can_release: boolean;
    apipay_invoice: {
      id: number | null;
      status: string;
      channel: "phone" | "qr";
      phone_number: string | null;
      qr_token_url: string | null;
      qr_image_url: string | null;
      qr_expires_at: string | null;
    } | null;
  }[];
  apipay_invoice: {
    payment_id: number;
    id: number | null;
    status: string;
    error_code: string | null;
    paid_at: string | null;
    channel: "phone" | "qr";
    phone_number: string | null;
    qr_token_url: string | null;
    qr_image_url: string | null;
    qr_expires_at: string | null;
    total_refunded: string;
  } | null;
  client_phone: string;
  receipt_available: boolean;
  truck_number: string;
  debt_requested: boolean;
  debt_override: boolean;
  created_at: string;
}
type PaymentStage = "requested" | "received" | "accountant_ok" | "confirmed" | "rejected";

export interface Payment {
  id: number;
  order: number;
  currency?: "KZT" | "USD";
  amount: string;
  method: PaymentMethod;
  method_label?: string;
  note?: string;
  status: PaymentStage;
  paid_at: string;
  recorded_by: number | null;
  recorded_by_name?: string | null;
  received_by_name?: string | null;
  received_at?: string | null;
  confirmed_by_name?: string | null;
  confirmed_at?: string | null;
  effective_status?: string;
  refunded_amount?: string;
  pending_refund_amount?: string;
  available_for_refund?: string;
  can_restore?: boolean;
  can_issue?: boolean;
  confirmation_mode?: "manual" | "automatic";
  refunds?: {
    id: number;
    amount: string;
    method: "apipay" | "cash";
    status: "pending" | "completed" | "failed";
    reason: string;
    requested_by_name: string | null;
    completed_at: string | null;
    created_at: string;
  }[];
  client_name?: string;
  provider?: {
    invoice_id: number | null;
    channel: "phone" | "qr";
    status: string;
    phone_number: string | null;
    qr_token_url: string | null;
    qr_image_url: string | null;
    qr_expires_at: string | null;
    total_refunded: string;
    available_for_refund: string;
    refunds: {
      id: number;
      amount: string;
      status: string;
      reason: string;
      error_code: string | null;
      created_at: string;
    }[];
  } | null;
}

export interface PaymentQueueItem extends Payment {
  client_name: string;
  department: string;
  department_name?: string;
  department_color?: string;
  order_status: string;
  store?: number | null;
  store_name?: string | null;
}
export interface CashierLogItem {
  id: number;
  message: string;
  user_name: string | null;
  order: number;
  client_name: string | null;
  store_name: string | null;
  payload: { payment_id?: number; amount?: string; method?: string; payment_stage?: string; action?: string };
  created_at: string;
  can_reopen: boolean;
  can_restore: boolean;
}
export interface StockItem {
  id: number;
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
  debt_currency?: "KZT" | "USD";
  debt_by_currency?: Record<string, string>;
  orders_count: number;
  unpaid_count: number;
  partial_count: number;
  stores_count: number;
  overdue_count: number;
}
export interface AiCountingSession {
  id: number;
  order_id: number;
  order_client_name: string;
  order_truck_number: string;
  camera: string;
  status: "starting" | "active";
  started_at: string;
  started_by_id: number | null;
  started_by_name: string;
  can_stop: boolean;
  last_status: { total?: number; weight?: number; status?: string; per_color?: Record<string, number> };
}
export interface AiCountingHistory {
  id: number;
  order_id: number;
  order_client_name: string;
  order_truck_number: string;
  camera: string;
  camera_name: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  started_by_id: number | null;
  started_by_name: string;
  final_total: number | null;
  last_status: { total?: number; weight?: number; status?: string; per_color?: Record<string, number> };
  has_recording: boolean;
  recording_available_until: string | null;
}
interface AiRecordingSegment {
  start: string;
  duration: number;
  video_url: string;
}
export interface AiRecording {
  available: boolean;
  detail?: string;
  retention_days?: number;
  segments: AiRecordingSegment[];
}
export interface ShippingBoardSettings {
  completed_orders_days: number;
  video_retention_days: number;
  updated_at: string | null;
}
export interface MonoblockCameraSettings {
  camera_sources: string[];
  locked: boolean;
  device_id: number | null;
  device_name: string | null;
  updated_at: string | null;
}
export interface MonoblockDevice {
  id: number;
  name: string;
  username: string;
  camera_source: string;
  camera_name: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}
export interface AlwaysOnProcessorStatus {
  cam: string;
  running: boolean;
  mode: "always_on" | "session" | "idle";
  recording: boolean;
  total: number;
  per_color?: Record<string, number>;
  last_frame_at?: string | null;
  error?: string | null;
  metrics?: { inference_fps?: number; dropped_frames?: number };
}
export interface AlwaysOnCameraSettings {
  camera_sources: string[];
  source: "sub" | "main";
  processors: AlwaysOnProcessorStatus[];
  capacity: number | null;
  service_available: boolean;
  sync_status: "synced" | "pending";
  detail: string;
  updated_at: string | null;
}
export interface WagonNumberCameraStatus {
  camera: string | null;
  source: "sub" | "main";
  stream: string | null;
  assigned: boolean;
  mode: "wagon_number_24_7";
}
export interface WagonNumberCameraSettings {
  camera_source: string | null;
  source: "main";
  live: WagonNumberCameraStatus | null;
  service_available: boolean;
  sync_status: "synced" | "pending";
  detail: string;
  updated_at: string | null;
}
export interface AlwaysOnColorAnalytics {
  color: string;
  total: number;
  percent: number;
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
interface AlwaysOnArchiveDay {
  day: string;
  model_total: number;
  adjustment: number;
  total: number;
  colors: AlwaysOnColorAnalytics[];
}
export interface AlwaysOnCountArchive {
  id: number;
  camera: string;
  period_start: string;
  period_end: string;
  model_total: number;
  adjustment: number;
  total: number;
  days: number;
  colors: AlwaysOnColorAnalytics[];
  /** Разбивка периода по дням — раскрывается по клику на строку архива. */
  day_rows: AlwaysOnArchiveDay[];
  note: string;
  archived_by_name: string | null;
  created_at: string;
}
export interface AlwaysOnDailyCameraAnalytics {
  camera: string;
  day: string;
  model_total: number;
  model_per_color: Record<string, number>;
  adjustment: number;
  total: number;
  all_time_total: number;
  history: AlwaysOnHistoryPoint[];
  colors: AlwaysOnColorAnalytics[];
  dominant_color: string | null;
  updated_at: string | null;
}
export interface AlwaysOnDailyAnalytics {
  day: string;
  total: number;
  all_time_total: number;
  /** Распознано моделью без ручных поправок — сумма цветов сходится с ним. */
  model_all_time_total: number;
  adjustment: number;
  history: AlwaysOnHistoryPoint[];
  colors: AlwaysOnColorAnalytics[];
  dominant_color: string | null;
  cameras: AlwaysOnDailyCameraAnalytics[];
}
export interface Permission {
  id: number;
  code: string;
  section: string;
  action: string;
  label: string;
}
export interface Role {
  id: number;
  name: string;
  description: string;
  is_system: boolean;
  permissions: Permission[];
  employee_count: number;
}
export interface Employee {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  phone: string;
  position: string;
  role: number | null;
  role_name: string | null;
  sales_department: number | null;
  sales_department_name: string | null;
  sales_department_color: string | null;
  name: string;
  /** Личные доступы поверх роли; права роли — в role_permissions. */
  permissions: string[];
  role_permissions: string[];
  denied_permissions: string[];
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

export interface EventLogPage {
  count: number;
  next: string | null;
  previous: string | null;
  results: EventLog[];
}

type TaskStatus = "pending" | "done";

interface TaskAttachment {
  id: number;
  kind: "photo" | "voice" | "file";
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
  created_at: string;
  updated_at: string;
}

export interface TaskAssignee {
  id: number;
  name: string;
  position: string;
}

/* ── Приход зерна ─────────────────────────────────────────────────────── */

export interface GrainWeighing {
  id: number;
  kind: "gross" | "tare";
  weight_kg: number;
  scale_number: string;
  source: "auto" | "manual";
  manual_reason: string;
  previous_weight_kg: number | null;
  operator_name: string | null;
  created_at: string;
}

export interface GrainLabCheck {
  id: number;
  moisture: string | null;
  impurity: string | null;
  nature: string | null;
  grain_class: string;
  infestation: boolean;
  damage: string;
  note: string;
  decision: "accepted" | "accepted_with_restrictions" | "rejected" | "quarantine";
  checked_by_name: string | null;
  created_at: string;
}

export interface GrainAllocation {
  id: number;
  silo: number;
  silo_name: string;
  amount_kg: number;
  measurement_source: string;
  created_at: string;
}

export interface GrainWagon {
  id: number;
  supply: number | null;
  number: string;
  number_source: "camera" | "manual";
  number_camera_source?: string;
  workflow: "simple" | "legacy";
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
  weight_difference_kg: number | null;
  weight_difference_percent: number | null;
  weight_matches: boolean | null;
  assigned_silo: number | null;
  assigned_silo_name: string | null;
  unloading_point?: string;
  unloading_started_at?: string | null;
  silo_arrived_at: string | null;
  unloading_finished_at?: string | null;
  unloading_paused?: boolean;
  exited_at: string | null;
  note?: string;
  created_at: string;
  weighings?: GrainWeighing[];
  lab_checks?: GrainLabCheck[];
  allocations?: GrainAllocation[];
}

export interface GrainSupply {
  id: number;
  supplier: string;
  grain_type: number | null;
  grain_type_name: string;
  grain_type_color: string | null;
  assigned_silo: number | null;
  assigned_silo_name: string | null;
  simple_flow: boolean;
  contract: string;
  culture: string;
  grain_class: string;
  expected_date: string | null;
  expected_total_kg: number | null;
  document_weight_kg: number | null;
  wagons_expected: number | null;
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
  sensor_estimated_kg: number | null;
  current_balance_kg: number;
  reserved_kg: number;
  free_capacity_kg: number;
  fill_percent: number;
  active_wagons: { id: number; number: string; status: string }[];
  sensor_difference_kg: number | null;
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

export type GrainType = GrainSiloType;

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
