export interface Me {
  id: number;
  username: string;
  is_client: boolean;
  is_superuser: boolean;
  permissions: string[];
  role_name: string | null;
  client_id: number | null;
}

export interface Grade { id: number; name: string; is_active: boolean; }
export interface Packaging { id: number; name: string; weight_kg: string; is_active: boolean; }
export interface Product {
  id: number; grade: number; packaging: number; price: string;
  is_active: boolean; label: string; weight_kg: string;
}
export interface Client {
  id: number; first_name: string; last_name: string; phone: string;
  name: string; country: string;
  iin: string; bank: string; bank_account: string; user: number | null;
}
export interface OrderItem { id?: number; product: number; product_label?: string; quantity: number; }
export interface Order {
  id: number; client: number; client_name?: string; client_phone?: string;
  status: string; truck_number: string; arrival_date?: string | null;
  items: OrderItem[]; total_amount: string; paid_total: string;
  is_fully_paid: boolean; debt_override: boolean;
  weigh_in_kg?: string | null; weigh_out_kg?: string | null; net_weight_kg?: string | null;
  created_at: string;
}
export interface Payment {
  id: number; order: number; amount: string; paid_at: string; recorded_by: number | null;
}
export interface StockItem {
  id: number; product: number; product_label: string;
  grade: string; packaging: string; weight_kg: string; bags: number;
}
export interface Shipment {
  id: number; order: number; truck_number: string;
  weigh_in_kg: string | null; weigh_out_kg: string | null;
  net_weight_kg: string | null; bags_loaded: number;
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
export interface Camera {
  id: number; name: string; camera_id: string;
  kind: "entry" | "counter" | "exit" | "";
  status: "pending" | "active";
  api_key: string; response_template: string;
  is_active: boolean; last_seen: string | null;
}
export interface WebhookCall {
  id: number; camera: number; plate: string;
  payload_bags: number | null; payload_weight: string | null;
  matched_order: number | null; decision: string; reason: string; created_at: string;
}
export interface EventLog {
  id: number; event_type: string; message: string;
  user: number | null; order: number | null; payload: Record<string, unknown>;
  created_at: string;
}
