export interface Me {
  id: number;
  username: string;
  is_client: boolean;
  is_superuser: boolean;
  roles: string[];
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
  name: string; country: string; requisites: string; user: number | null;
}
export interface OrderItem { id?: number; product: number; quantity: number; }
export interface Order {
  id: number; client: number; status: string; truck_number: string;
  items: OrderItem[]; total_amount: string; paid_total: string;
  is_fully_paid: boolean; debt_override: boolean; created_at: string;
}
export interface Payment {
  id: number; order: number; amount: string; paid_at: string; recorded_by: number | null;
}
export interface StockItem { id: number; product: number; product_label: string; bags: number; }
export interface Shipment {
  id: number; order: number; truck_number: string;
  weigh_in_kg: string | null; weigh_out_kg: string | null;
  net_weight_kg: string | null; bags_loaded: number;
  arrived_at: string | null; shipped_at: string | null;
}
export interface EventLog {
  id: number; event_type: string; message: string;
  user: number | null; order: number | null; payload: Record<string, unknown>;
  created_at: string;
}
