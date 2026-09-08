import { Badge } from "@/components/ui/badge";
import type { ShippingTransportTracking } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

const reasons: Record<string, string> = {
  model_unavailable: "Детектор транспорта не настроен или недоступен",
  body_detector_unavailable: "Детектор транспорта не настроен или недоступен",
  camera_unavailable: "Камера транспорта недоступна",
  frame_unavailable: "Нет кадра для определения присутствия транспорта",
  frame_unusable: "Нет пригодного кадра для определения присутствия транспорта",
  no_frame: "Нет кадра для определения присутствия транспорта",
  stale_frame: "Нет свежего кадра транспорта",
  non_fresh_frame: "Нет свежего кадра транспорта",
  stale_observation: "Нет свежих данных о присутствии транспорта",
  stale_tracking: "Нет свежих данных о присутствии транспорта",
  inference_error: "Не удалось определить присутствие транспорта",
  body_inference_failed: "Не удалось определить присутствие транспорта",
  invalid_body_detection: "Не удалось определить присутствие транспорта",
  invalid_image: "Не удалось обработать кадр транспорта",
  multiple_transports: "В кадре несколько транспортных средств",
  different_transport_type: "Тип транспорта в кадре не соответствует настройке камеры",
  confirming_presence: "Подтверждаем присутствие транспорта по нескольким кадрам",
  confirming_absence: "Проверяем отсутствие транспорта по нескольким кадрам",
};

/** Only a body detector can report presence. Reading a number is not presence evidence. */
export function ShippingTransportTrackingDetails({
  tracking,
  alert,
  historical = false,
  stale = false,
}: {
  tracking?: ShippingTransportTracking | null;
  alert?: string | null;
  historical?: boolean;
  stale?: boolean;
}) {
  const bodyTracking = tracking?.schema_version === 1 && tracking.basis === "transport_body" ? tracking : null;
  const unavailable = !bodyTracking || (!historical && stale);
  const presence = unavailable ? "unknown" : bodyTracking.presence;
  const motion = unavailable ? "unknown" : bodyTracking.motion;
  const label =
    presence === "present"
      ? motion === "stationary"
        ? "Транспорт стоит"
        : motion === "moving"
          ? "Транспорт движется"
          : "Транспорт обнаружен"
      : presence === "absent"
        ? "Транспорт не обнаружен"
        : "Присутствие неизвестно";
  const detail =
    !historical && stale
      ? "Нет свежих данных о присутствии транспорта"
      : !bodyTracking
        ? historical
          ? "Сведения о присутствии транспорта для этого наблюдения не сохранены"
          : "Нет данных о присутствии транспорта"
        : reasons[bodyTracking.reason] ||
          (presence === "unknown" ? "Нет достоверных данных о присутствии транспорта" : "");

  return (
    <div
      className="space-y-2 rounded-md bg-[var(--muted)]/40 p-3"
      role="group"
      aria-label={historical ? "Транспорт на момент наблюдения" : "Транспорт у конвейера"}
    >
      {historical && <p className="text-xs text-[var(--muted-foreground)]">На момент наблюдения</p>}
      <Badge tone={presence === "present" ? "primary" : "muted"} dot>
        {label}
      </Badge>
      {detail && <p className="text-xs text-[var(--muted-foreground)]">{detail}</p>}
      {bodyTracking?.observed_at && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Наблюдение: <time dateTime={bodyTracking.observed_at}>{formatDateTime(bodyTracking.observed_at)}</time>
        </p>
      )}
      {!unavailable && bodyTracking?.present_since && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Обнаружен: <time dateTime={bodyTracking.present_since}>{formatDateTime(bodyTracking.present_since)}</time>
        </p>
      )}
      {presence === "present" && motion === "stationary" && bodyTracking?.stationary_since && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Стоит с: <time dateTime={bodyTracking.stationary_since}>{formatDateTime(bodyTracking.stationary_since)}</time>
        </p>
      )}
      {presence === "absent" && bodyTracking?.absent_since && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Не обнаружен с: <time dateTime={bodyTracking.absent_since}>{formatDateTime(bodyTracking.absent_since)}</time>
        </p>
      )}
      {historical && bodyTracking?.last_seen_at && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Последнее обнаружение:{" "}
          <time dateTime={bodyTracking.last_seen_at}>{formatDateTime(bodyTracking.last_seen_at)}</time>
        </p>
      )}
      {presence === "present" && bodyTracking?.number_associated === false && (
        <p className="text-xs text-[var(--muted-foreground)]">Номер ещё не связан с обнаруженным транспортом</p>
      )}
      {alert && (
        <p role="alert" className="rounded-md border border-[var(--warning)]/40 bg-[var(--warning)]/10 p-2 text-xs">
          {alert}
        </p>
      )}
    </div>
  );
}
