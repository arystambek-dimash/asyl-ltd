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

const ARCH_STOP_REASONS: Record<string, string> = {
  silo_required: "Назначьте силос в рейсе — заезд запишется автоматически",
  wagon_on_site: "Вагон с этим номером уже на территории",
  exit_not_lower: "Вес на выезде не меньше веса на въезде — проверьте взвешивания",
  no_exit_weight: "Перед отъездом не было устойчивого веса — укажите выезд вручную",
  invalid_wagon_transition: "Рейс в состоянии, где вес применить нельзя",
  exit_unseen: "Отъезд не был виден — укажите выезд вручную",
  wagon_deleted: "Рейс удалён — стоянку можно закрыть",
  import_error: "Ошибка импорта — проверьте рейс",
  not_simple_flow: "Рейс не в коротком потоке — оформите вручную",
  wrong_scale_action: "Весы ждут другое действие — проверьте этап рейса",
};

export function archStopReasonLabel(reason: string, detail?: string) {
  if (!reason) return "";
  return ARCH_STOP_REASONS[reason] || detail || "Требуется проверка";
}

export function identityReviewLabel(reason?: string, orientation?: string) {
  const labels: Record<string, string> = {
    entry_missing:
      orientation === "front"
        ? "Не удалось подтвердить номер для создания заезда"
        : "Не найден подходящий заезд или сохранённая тара",
    saved_tare_missing: "Номер определён, но открытого заезда и сохранённой тары нет",
    previous_exit_missing:
      "У этой машины ещё открыт рейс без выезда — сначала привяжите его выезд, заезд оформится сам",
    plate_unreadable: "Номер не прочитан после автоматического распознавания и проверки ИИ",
    plate_unclear: "ИИ не смог уверенно прочитать все символы номера",
    photo_unavailable: "Кадр этого взвешивания недоступен — ИИ не может проверить номер",
    photo_not_bound: "Кадр не связан со взвешиванием — требуется проверка",
    orientation_unknown: "Номер проверен, но направление проезда не определено",
    ambiguous_active_passage: "Для номера найдено несколько открытых рейсов — требуется сверка",
    passage_time_conflict: "Время взвешивания не соответствует найденному рейсу",
    exit_weight_not_greater: "Вес выезда не больше сохранённой тары — требуется проверка",
    booking_conflict: "Не удалось оформить рейс автоматически — требуется проверка",
    verification_window_expired: "Срок автоматической проверки истёк — взвешивание сохранено",
    image_binding_conflict: "ИИ противоречиво определил фотографии — требуется сверка",
    appearance_unconfirmed: "Номер прочитан, но соответствие машины по фото не подтверждено",
  };
  return labels[reason || ""] || "ИИ: нужна ручная проверка номера и машины";
}
