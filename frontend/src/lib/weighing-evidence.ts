export function photoStatusLabel(status?: string) {
  if (status === "pending") return "Фото ожидается";
  if (status === "retrying") return "Фото загружается повторно";
  if (status === "saved") return "Фото сохранено";
  return "Фото недоступно";
}

const REASONS: Record<string, string> = {
  identity_verification_required: "Вес сохранён — требуется проверка номера и фото машины",
  entry_missing: "выезд без заезда: рейс с этим номером не найден",
  plate_unreadable: "Номер не распознан — выберите рейс по фото и времени",
  open_passages_exist: "Номер не распознан — требуется привязка к рейсу",
  orientation_unknown: "Направление проезда не определено — требуется проверка",
  open_trip_conflict: "Новый заезд при незавершённом рейсе — требуется проверка",
  passage_time_conflict: "Время взвешивания не соответствует рейсу",
  exit_weight_not_greater: "Вес выезда не больше веса въезда",
  entry_exit_too_close: "Слишком короткий интервал между въездом и выездом",
  recent_completed_passage: "Для этого номера недавно завершён рейс",
  vehicle_recognition_vehicle_left: "Машина съехала до повторного распознавания",
  vehicle_recognition_after_departure: "Кадр после освобождения весов — требуется проверка",
  vehicle_recognition_window_missed: "Обработка камеры не началась вовремя",
  vehicle_recognition_attempts_exhausted: "Номер не прочитан после повторных попыток",
  vehicle_recognition_unavailable: "Камера недоступна",
  automatic_scale_not_stable: "Машина съехала до подтверждения стабильного веса",
  truck_scale_observation_lost: "Связь с весами прервалась до подтверждения веса",
};

export function weighingReasonLabel(reason: string, detail?: string) {
  return REASONS[reason] || detail || (reason ? "Требуется проверка взвешивания" : "");
}

export function identityReviewLabel(reason?: string) {
  const labels: Record<string, string> = {
    entry_missing: "Нет подходящего открытого заезда с фото — выберите рейс или сохранённую тару",
    plate_unclear: "ИИ не смог уверенно прочитать все символы номера",
    image_binding_conflict: "ИИ противоречиво определил фотографии — требуется сверка",
    appearance_unconfirmed: "Номер прочитан, но соответствие машины по фото не подтверждено",
  };
  return labels[reason || ""] || "ИИ: нужна ручная проверка номера и машины";
}
