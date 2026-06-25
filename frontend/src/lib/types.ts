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
  weight_kg: string; price: string; is_active: boolean; label: string; cv_class: string;
}
export interface Client {
  id: number; first_name: string; last_name: string; phone: string;
  name: string; country: string;
  iin: string; bank: string; bank_account: string; user: number | null;
  debt_total?: string;
}
export interface Store {
  id: number; client: number; name: string; address: string; phone: string;
  payment_schedule_type: "none" | "monthly" | "weekly";
  payment_days: number[]; contract_signed_at: string | null;
}
export interface Notification {
  id: number; text: string; is_read: boolean; created_at: string;
}
export interface OrderItem { id?: number; product: number; product_label?: string; cv_class?: string; quantity: number; }
export interface StatusChangeRequest {
  id: number; order: number; to_status: string; to_status_label?: string;
  status: string; requested_by?: number | null; requested_by_name?: string | null;
  decided_by?: number | null; created_at: string; decided_at?: string | null;
}
export interface Order {
  id: number; client: number; store?: number | null; client_name?: string; client_phone?: string;
  status: string; payment_status?: string; settlement_intent?: string;
  truck_number: string; truck_number_set_by?: number | null;
  arrival_date?: string | null;
  items: OrderItem[]; total_amount: string; paid_total: string; remaining_amount?: string;
  is_fully_paid: boolean; debt_override: boolean; debt_requested?: boolean;
  pending_status_requests?: StatusChangeRequest[];
  weigh_in_kg?: string | null;
  bags_loaded?: number; bag_estimate_kg?: string;
  bag_weight_kg?: string; debt_override_by_name?: string | null;
  created_at: string;
}
export interface Payment {
  id: number; order: number; amount: string; method: string; status: string;
  paid_at: string; recorded_by: number | null;
}
export interface StockItem {
  id: number; product: number; product_label: string;
  grade: string; packaging: string; weight_kg: string; bags: number;
}
export interface Shipment {
  id: number; order: number; truck_number: string;
  weigh_in_kg: string | null; bags_loaded: number;
  arrived_at: string | null; shipped_at: string | null;
}
export interface Permission { id: number; code: string; section: string; action: string; label: string; }
export interface Role {
  id: number; name: string; description: string; is_system: boolean;
  permissions: Permission[]; employee_count: number;
}
export interface Employee {
  id: number; username: string; first_name: string; last_name: string;
  phone: string; position: string; role: number | null; role_name: string | null;
  name: string; is_active: boolean;
}
export interface EventLog {
  id: number; event_type: string; message: string;
  user: number | null; order: number | null; payload: Record<string, unknown>;
  created_at: string;
}
