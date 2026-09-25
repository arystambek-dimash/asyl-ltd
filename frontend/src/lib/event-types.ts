import {
  Activity,
  ArrowDownToLine,
  ArrowLeftRight,
  CalendarClock,
  Camera,
  CircleDot,
  CopyPlus,
  Forklift,
  ListTodo,
  MessageCircle,
  Package,
  PackageCheck,
  Pencil,
  RotateCcw,
  Scale,
  ShieldCheck,
  TrainFront,
  Trash2,
  Truck,
  UsersRound,
  Wallet,
  Warehouse,
  Wheat,
  type LucideIcon,
} from "lucide-react";

type EventTypeMeta = {
  label: string;
  icon: LucideIcon;
  /** базовый цвет события как CSS-переменная темы */
  color: string;
};

type EventTypeGroup = {
  label: string;
  types: Record<string, EventTypeMeta>;
};

const ring = "var(--ring)";
const warning = "var(--warning)";
const success = "var(--success)";
const destructive = "var(--destructive)";
const muted = "var(--muted-foreground)";

/**
 * Все типы EventLog.event_type, которые пишет backend (`log_event`), и типы
 * старых записей журнала — по разделам журнала. Отсюда берутся подпись и
 * значок события в журнале, список фильтра «Тип события» и запасная подпись
 * в истории заказа.
 */
export const EVENT_TYPE_GROUPS: readonly EventTypeGroup[] = [
  {
    label: "Заказы",
    types: {
      status: { label: "Статус", icon: CircleDot, color: ring },
      status_override: { label: "Ручная смена статуса", icon: CircleDot, color: warning },
      status_request: { label: "Запрос статуса", icon: CircleDot, color: warning },
      order_edit: { label: "Правка заказа", icon: Pencil, color: ring },
      order_price_correction: { label: "Корректировка стоимости", icon: Pencil, color: warning },
      order_repeat: { label: "Повтор заказа", icon: CopyPlus, color: ring },
      order_backdated: { label: "Задним числом", icon: CalendarClock, color: warning },
      order: { label: "Корзина заказов", icon: Trash2, color: destructive },
      // Типы старых записей журнала: backend их больше не пишет
      // (arrival — прежний код, order_department_cleanup — миграция orders 0036).
      arrival: { label: "Прибытие", icon: Truck, color: ring },
      order_department_cleanup: { label: "Отдел заявки очищен", icon: Pencil, color: muted },
    },
  },
  {
    label: "Оплаты и долги",
    types: {
      payment: { label: "Оплата", icon: Wallet, color: success },
      debt_override: { label: "Согласование долга", icon: Scale, color: destructive },
      debt: { label: "Отгрузка в долг", icon: Scale, color: destructive },
    },
  },
  {
    label: "Отгрузка",
    types: {
      loading_start: { label: "Начало загрузки", icon: Forklift, color: warning },
      camera_bound: { label: "Камера за заказом", icon: Camera, color: ring },
      loading: { label: "Загрузка", icon: Forklift, color: warning },
      loading_done: { label: "Загрузка завершена", icon: Forklift, color: success },
      shipment: { label: "Отгрузка", icon: ArrowDownToLine, color: ring },
      shipment_rollback: { label: "Откат отгрузки", icon: ArrowDownToLine, color: destructive },
      shipping_rewind: { label: "Сброс отгрузки", icon: RotateCcw, color: destructive },
      shipping_loading_identified: { label: "Транспорт распознан", icon: Truck, color: ring },
      shipping_idle_timeout_changed: { label: "Таймаут простоя", icon: Camera, color: muted },
    },
  },
  {
    label: "Склад",
    types: {
      receipt: { label: "Приёмка", icon: PackageCheck, color: ring },
      stock_adjust: { label: "Корректировка склада", icon: Warehouse, color: warning },
      stock_transfer: { label: "Перемещение", icon: ArrowLeftRight, color: ring },
      stock_negative: { label: "Списание в минус", icon: Warehouse, color: destructive },
    },
  },
  {
    label: "Камеры AI 24/7",
    types: {
      always_on_stock_posted: { label: "AI 24/7: приход", icon: PackageCheck, color: success },
      always_on_stock_blocked: { label: "AI 24/7: приход не проведён", icon: PackageCheck, color: destructive },
      always_on_unknown_color_assigned: { label: "AI 24/7: цвет мешков", icon: Camera, color: ring },
      always_on_count_archived: { label: "AI 24/7: счётчик обнулён", icon: Camera, color: muted },
      always_on_count_adjustment: { label: "AI 24/7: корректировка", icon: Camera, color: warning },
      always_on_archive_deleted: { label: "AI 24/7: запись архива удалена", icon: Camera, color: destructive },
      camera_settings: { label: "Настройки камеры", icon: Camera, color: muted },
    },
  },
  {
    label: "Зерно и вывоз",
    types: {
      grain_supply: { label: "Приход вагонов", icon: TrainFront, color: ring },
      grain_arrival: { label: "Прибытие рейса", icon: TrainFront, color: ring },
      grain_arch: { label: "Арка", icon: TrainFront, color: ring },
      grain_passage: { label: "Заезд на вывоз", icon: Truck, color: ring },
      grain_weighing: { label: "Взвешивание", icon: Scale, color: ring },
      grain_status: { label: "Статус рейса", icon: Wheat, color: ring },
      grain_number: { label: "Номер рейса", icon: Pencil, color: warning },
      grain_silo: { label: "Силос рейса", icon: Wheat, color: ring },
      grain_unloading: { label: "Разгрузка", icon: Wheat, color: success },
      grain_automatic_binding: { label: "Автопривязка весов", icon: Scale, color: ring },
      grain_identity_verified: { label: "Госномер подтверждён", icon: ShieldCheck, color: success },
      grain_tare_reference: { label: "Тара из истории", icon: Scale, color: warning },
      grain_manual_entry: { label: "Ручной заезд", icon: Pencil, color: warning },
      grain_exit_weight_corrected: { label: "Правка веса выезда", icon: Pencil, color: warning },
      grain_unassigned_weighing: { label: "Неопознанное взвешивание", icon: Scale, color: warning },
      grain_unassigned_weighing_discarded: { label: "Взвешивание отклонено", icon: Scale, color: destructive },
      grain_wagon_deleted: { label: "Рейс удалён", icon: Trash2, color: destructive },
      grain_adjust: { label: "Корректировка силоса", icon: Wheat, color: warning },
      grain_auto_scale_settings_updated: { label: "Настройки автовесов", icon: Scale, color: muted },
      grain_automatic_scale_acknowledged: { label: "Сбой автовесов", icon: Scale, color: destructive },
      // Запись миграции grain 0019: backend её больше не пишет.
      grain_tare_memory_backfill: { label: "Память тары восстановлена", icon: Scale, color: muted },
    },
  },
  {
    label: "Отчёты о вагонах",
    types: {
      rail_report: { label: "Отчёт о вагонах", icon: TrainFront, color: ring },
      whatsapp_bot: { label: "WhatsApp-бот", icon: MessageCircle, color: ring },
      clients: { label: "Клиенты в отчётах", icon: UsersRound, color: muted },
    },
  },
  {
    label: "Справочники и люди",
    types: {
      catalog: { label: "Товары", icon: Package, color: muted },
      client: { label: "Клиент", icon: UsersRound, color: ring },
      client_security: { label: "Доступ клиента", icon: ShieldCheck, color: warning },
      client_statement: { label: "Выписка клиента", icon: UsersRound, color: muted },
      clients_statement: { label: "Общая выписка", icon: UsersRound, color: muted },
      employee_profile: { label: "Профиль сотрудника", icon: UsersRound, color: muted },
      employee_security: { label: "Доступ сотрудника", icon: ShieldCheck, color: warning },
      task: { label: "Задача", icon: ListTodo, color: ring },
      // Запись миграции cameras 0031 (отключение учёток моноблока): backend её больше не пишет.
      monoblock_device: { label: "Учётка моноблока", icon: ShieldCheck, color: muted },
    },
  },
];

const EVENT_TYPES: Record<string, EventTypeMeta> = Object.assign(
  {},
  ...EVENT_TYPE_GROUPS.map((group) => group.types),
) as Record<string, EventTypeMeta>;

/** Подпись и значок события; неизвестный тип показывается своим кодом. */
export function eventTypeMeta(eventType: string): EventTypeMeta {
  return EVENT_TYPES[eventType] ?? { label: eventType, icon: Activity, color: muted };
}
