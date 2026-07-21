export interface Me {
  id: number;
  username: string;
  is_client: boolean;
  is_superuser: boolean;
  permissions: string[];
  role_name: string | null;
  client_id: number | null;
}

export interface Product {
  id: number; name: string; color: "Red" | "Green" | "Blue"; color_label: string;
  weight_kg: string; is_active: boolean; label: string; cv_class: string;
  available_bags?: number;
  ask_truck_weight?: boolean;
}
export interface ClientPriceRow {
  product: number;
  product_label: string;
  price: string | null;
  updated_at: string | null;
  updated_by_name: string | null;
}
export interface ClientPriceSheet {
  client: Client;
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
  revenue: string;
}
export type PortalPaymentMethod = "pending" | "invoice" | "kaspi" | "cash" | "debt";
export type PaymentMethod = PortalPaymentMethod | "card";

export interface Client {
  id: number; first_name: string; last_name: string; phone: string;
  name: string; company_name: string; country: string;
  currency: "KZT" | "USD";
  iin: string; bank: string; bank_account: string; user: number | null;
  debt_total?: string; created_at?: string;
}
export interface Store {
  id: number; client: number; name: string; address: string; phone: string;
  payment_schedule_type: "none" | "monthly" | "weekly";
  payment_days: number[]; contract_signed_at: string | null;
}
export interface Notification {
  id: number; text: string; is_read: boolean; created_at: string;
}
export interface OrderItem { id?: number; product: number | null; product_label?: string; cv_class?: string; quantity: number; price?: string | null; unit_price?: string | null; client_price?: string | null; weight_kg?: string | null; ask_truck_weight?: boolean; }
export interface StatusChangeRequest {
  id: number; order: number; to_status: string; to_status_label?: string;
  status: string; requested_by?: number | null; requested_by_name?: string | null;
  decided_by?: number | null; created_at: string; decided_at?: string | null;
}
export interface Order {
  id: number; client: number; store?: number | null; client_name?: string; client_phone?: string;
  department?: string;
  department_name?: string;
  department_color?: string;
  currency: "KZT" | "USD";
  status: string; payment_status?: string; settlement_intent?: string;
  payment_method?: PaymentMethod; transport_type?: "truck" | "train";
  truck_number: string; truck_number_set_by?: number | null;
  arrival_date?: string | null;
  notes?: string;
  items: OrderItem[]; total_amount: string; paid_total: string; remaining_amount?: string;
  has_pending_payment?: boolean;
  is_fully_paid: boolean; is_debt?: boolean; debt_override: boolean; debt_requested?: boolean;
  pending_status_requests?: StatusChangeRequest[];
  payments?: Payment[];
  pending_payments?: Payment[];
  weigh_in_kg?: string | null;
  bags_loaded?: number; bag_estimate_kg?: string;
  bag_weight_kg?: string; debt_override_by_name?: string | null;
  created_at: string;
  shipped_at?: string | null;
  loading_camera?: string;
  deleted_at?: string | null; deleted_by_name?: string | null;
}

/** Client-portal projection: prices are deliberately hidden until confirmation. */
export interface PortalOrder {
  id: number;
  status: string;
  payment_status?: string;
  settlement_intent: string;
  payment_method: PortalPaymentMethod;
  currency: "KZT" | "USD";
  transport_type: "truck" | "train";
  store: number | null;
  store_name: string | null;
  items: OrderItem[];
  total_amount: string | null;
  paid_total: string | null;
  remaining_amount: string | null;
  has_pending_payment: boolean;
  truck_number: string;
  debt_requested: boolean;
  debt_override: boolean;
  created_at: string;
}
export type PaymentStage = "requested" | "received" | "accountant_ok" | "confirmed" | "rejected";

export interface Payment {
  id: number; order: number; amount: string; method: PaymentMethod; method_label?: string;
  note?: string;
  status: PaymentStage; paid_at: string; recorded_by: number | null; recorded_by_name?: string | null;
  received_by_name?: string | null; received_at?: string | null;
  confirmed_by_name?: string | null; confirmed_at?: string | null;
}

export interface PaymentQueueItem extends Payment {
  client_name: string; department: string; department_name?: string;
  department_color?: string; order_status: string;
  store?: number | null; store_name?: string | null;
}
export interface CashierLogItem {
  id: number; message: string; user_name: string | null; order: number;
  client_name: string | null; store_name: string | null;
  payload: { payment_id?: number; amount?: string; method?: string; payment_stage?: string; action?: string };
  created_at: string; can_reopen: boolean;
}
export interface StockItem {
  id: number; product: number; product_label: string;
  grade: string; color: string; color_label: string;
  packaging: string; weight_kg: string; bags: number;
}
/** Строка агрегата GET /clients/debts/. */
export interface ClientDebt {
  client_id: number;
  client_name: string;
  client_phone: string;
  debt_total: string;
  orders_count: number;
  unpaid_count: number;
  partial_count: number;
  stores_count: number;
  overdue_count: number;
}
export interface AiCountingSession {
  id: number; order_id: number; order_client_name: string; order_truck_number: string;
  camera: string; status: "starting" | "active"; started_at: string;
  started_by_id: number | null; started_by_name: string; can_stop: boolean;
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
export interface AiRecordingSegment {
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
  updated_at: string | null;
}
export interface AlwaysOnProcessorStatus {
  cam: string;
  running: boolean;
  mode: "always_on" | "session" | "idle";
  recording: boolean;
  total: number;
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
export interface Permission { id: number; code: string; section: string; action: string; label: string; }
export interface Role {
  id: number; name: string; description: string; is_system: boolean;
  permissions: Permission[]; employee_count: number;
}
export interface Employee {
  id: number; username: string; first_name: string; last_name: string;
  phone: string; position: string; role: number | null; role_name: string | null;
  name: string;
  /** Личные доступы поверх роли; права роли — в role_permissions. */
  permissions: string[];
  role_permissions: string[];
  denied_permissions: string[];
  is_active: boolean;
}
export interface EventLog {
  id: number; event_type: string; message: string;
  user: number | null; user_name: string | null; order: number | null; payload: Record<string, unknown>;
  created_at: string;
}
