export interface ShippingSegment {
  id: number;
  number: string;
  number_source: "model" | "gpt" | "manual" | "";
  identity_status: "pending" | "processing" | "identified" | "unidentified";
  identity_error: string;
  started_at: string;
  last_counted_at: string;
  ended_at: string | null;
  total_bags: number;
  photo_url: string | null;
  photo_taken_at: string | null;
  idle_timeout_seconds: number;
  can_identify: boolean;
}

export interface ShippingSession {
  id: number;
  camera: string;
  recognition_model: "vehicle_number" | "wagon_number" | "";
  number: string;
  status: "active" | "closed";
  total_bags: number;
  started_at: string;
  last_counted_at: string;
  ended_at: string | null;
  order_id: number | null;
  segments: ShippingSegment[];
}

export interface ShippingSessionsPage {
  results: ShippingSession[];
  next_cursor: string | number | null;
}

export interface ShippingSessionSettings {
  idle_timeout_seconds: number;
  can_manage: boolean;
}

export interface ShippingSegmentDetail extends ShippingSegment {
  camera: string;
  recognition_model: ShippingSession["recognition_model"];
  session_id: number;
  order_id: number | null;
}

export function shippingNumberSource(source: ShippingSegment["number_source"]): string {
  return { model: "Модель", gpt: "GPT", manual: "Вручную", "": "Не определён" }[source] ?? "Не определён";
}

export function shippingIdentityLabel(segment: ShippingSegment): string {
  if (segment.number) return `Номер: ${shippingNumberSource(segment.number_source)}`;
  if (segment.identity_status === "pending" || segment.identity_status === "processing") return "Распознаём номер";
  return "Номер не определён";
}

export function shippingIdentityError(reason: string): string {
  const labels: Record<string, string> = {
    photo_unavailable: "Кадр номера недоступен.",
    photo_missing: "Кадр номера не сохранён.",
    photo_window_expired: "Событие пришло с задержкой: кадр этой погрузки уже недоступен.",
    photo_capture_interrupted: "Получение кадра прервано.",
    photo_storage_unavailable: "Кадр номера не удалось сохранить.",
    loading_zone_invalid: "Проверьте зону распознавания в настройках камеры номера.",
    plate_unreadable: "Номер не удалось прочитать.",
    number_unreadable: "Номер не удалось прочитать.",
    no_number_camera: "Не назначена камера номера.",
    camera_not_configured: "Не назначена камера номера.",
    number_camera_not_configured: "Не назначена камера номера.",
    gpt_unavailable: "Проверка номера через GPT временно недоступна.",
    fallback_not_configured: "Проверка номера через GPT не настроена.",
    fallback_unavailable: "Проверка номера через GPT временно недоступна.",
    identity_retry_exhausted: "Повторные попытки распознавания не определили номер.",
    budget_exceeded: "Лимит проверки номера исчерпан.",
    identity_conflict: "Результаты распознавания номера различаются.",
  };
  return labels[reason] ?? "Автоматическое распознавание не определило номер.";
}

export function shippingSegmentPrintHref(id: number): string {
  return `/monoblock/shipping-segments/${id}/print`;
}
