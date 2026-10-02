import { api } from "@/lib/api";
import type { TransportPair } from "@/lib/plates";
import type { ShipmentWagon } from "@/lib/types";
import type { WagonReportDelivery } from "@/lib/wagon-report";
import { downloadBlob } from "@/lib/download";
import { readStoredChoice, storeChoice, userChoiceKey } from "@/lib/stored-choice";
import { formatMoney, formatTons } from "@/lib/utils";

export interface LoaderOrderItem {
  label: string;
  quantity: number;
  weight_kg: string;
  unit_price: string | null;
  /** Цвет мешка (Red/Green/Blue); у удалённого товара — пусто. */
  color: string;
}

/** Заказ на экране грузчика (GET /loader/queue/ и /loader/history/). */
export interface LoaderOrder {
  id: number;
  status: string;
  transport_type: "truck" | "train";
  truck_number: string;
  trailer_number: string;
  /** Прошлые пары клиента — чипы «как в прошлый раз». */
  transport_suggestions: TransportPair[];
  /** Пару указал клиент: грузчик её не меняет. */
  transport_locked: boolean;
  currency: "KZT" | "USD";
  /** Плановый день: дата приезда, иначе день создания (ГГГГ-ММ-ДД). */
  planned_on: string;
  client_name: string;
  /** Страна клиента — страна номера по умолчанию. */
  client_country: string;
  items: LoaderOrderItem[];
  bags: number;
  total_kg: string;
  total_amount: string;
  shipped_at: string | null;
  /** Оплата заказа: грузчик видит, платил ли клиент заранее. */
  payment_status: string;
  remaining_amount: string;
  /** Грузчик может сам отменить эту отгрузку (своя и не старше часа). */
  can_rollback: boolean;
  /** Отгрузка по отчёту о вагонах: станция и вагоны. */
  rail_station: string;
  wagons: ShipmentWagon[];
  /** «Отправить отчёт» о вагонах: когда и что с отчётом у каждого получателя. */
  report_sent_at: string | null;
  report_deliveries: WagonReportDelivery[];
}

export interface WaybillSigner {
  role: string;
  name: string;
}

export interface WaybillSettings {
  point_name: string;
  signers: WaybillSigner[];
}

/** Вес груза: у фуры — килограммы, у вагона — тонны (1360 мешков по 50 кг = 68 т). */
export function loadWeight(order: Pick<LoaderOrder, "transport_type" | "total_kg">): string {
  const kg = Number(order.total_kg);
  return order.transport_type === "train" ? `${formatTons(kg)} т` : `${formatMoney(kg)} кг`;
}

/** Последняя вкладка «Фуры | Вагоны» — своя у каждого на общем планшете. */
export function readStoredLoaderTransport(userId: number): string | null {
  return readStoredChoice(userChoiceKey("loader:transport", userId));
}

export function storeLoaderTransport(transport: LoaderOrder["transport_type"], userId: number) {
  storeChoice(userChoiceKey("loader:transport", userId), transport);
}

/** «Просрочено» под «Сегодня»: за сколько последних дней. Весь хвост — отдельной кнопкой, иначе спам. */
export const RECENT_OVERDUE_DAYS = [1, 3, 7, 14] as const;
/** Окно, пока грузчик не выбрал своё. */
export const DEFAULT_RECENT_OVERDUE_DAYS = 3;

const overdueDaysKey = (transport: LoaderOrder["transport_type"], userId: number) =>
  userChoiceKey(`loader:overdue-days:${transport}`, userId);

/** Окно просрочки — своё у каждого на общем планшете и у каждой вкладки «Фуры | Вагоны». */
export function readStoredOverdueDays(transport: LoaderOrder["transport_type"], userId: number): number {
  const stored = Number(readStoredChoice(overdueDaysKey(transport, userId)));
  return RECENT_OVERDUE_DAYS.find((days) => days === stored) ?? DEFAULT_RECENT_OVERDUE_DAYS;
}

export function storeOverdueDays(days: number, transport: LoaderOrder["transport_type"], userId: number) {
  storeChoice(overdueDaysKey(transport, userId), String(days));
}

type LoaderPath = "queue" | "history" | "wagon-report/compose" | "truck-report";

export function loaderUrl(path: LoaderPath, params: Record<string, string>) {
  const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value));
  const suffix = query.toString();
  return `/loader/${path}/${suffix ? `?${suffix}` : ""}`;
}

/** Отчёт по истории грузчика: одна отгрузка или вся история с фильтрами экрана (период и поиск). */
export type HistoryReportScope = { order: number } | { date_from: string; date_to: string; search: string };

/** «Отправить отчёт» вагонов и «Скопировать отчёт» фур: ``?order=`` или фильтры истории. */
export function historyReportUrl(path: "wagon-report/compose" | "truck-report", scope: HistoryReportScope) {
  const params: Record<string, string> =
    "order" in scope
      ? { order: String(scope.order) }
      : { date_from: scope.date_from, date_to: scope.date_to, search: scope.search };
  return loaderUrl(path, params);
}

/** «Скопировать отчёт» — у отгруженной фуры; вагоны отчитываются ботом («Отправить отчёт»). */
export function hasTruckReport(order: Pick<LoaderOrder, "transport_type" | "status">): boolean {
  return order.transport_type === "truck" && order.status === "shipped";
}

/** «Скопировать отчёт» у фур: текст для чата отгрузок; составляет его сервер (GET /loader/truck-report/). */
export async function truckReportText(scope: HistoryReportScope): Promise<string> {
  const { data } = await api.get<{ text: string }>(historyReportUrl("truck-report", scope));
  return data.text;
}

/**
 * Открыть накладную для печати. Вкладку открываем сразу по нажатию — после
 * ожидания ответа браузер счёл бы её всплывающим окном и заблокировал.
 */
export async function openWaybill(orderId: number) {
  const tab = window.open("", "_blank");
  try {
    const { data } = await api.get<Blob>(`/loader/orders/${orderId}/waybill/`, { responseType: "blob" });
    if (!tab) {
      downloadBlob(data, `nakladnaya_${orderId}.pdf`);
      return;
    }
    const url = URL.createObjectURL(data);
    tab.location.href = url;
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (error) {
    tab?.close();
    throw error;
  }
}

/** Склад на опроснике «С какого склада?». */
export interface DispatchWarehouse {
  id: number;
  name: string;
}

/** Товар заказа на опроснике: все его мешки по всем позициям. */
export interface DispatchSourceProduct {
  product: number;
  label: string;
  color: string;
  bags: number;
  /** Остаток склада (ключ — id склада) — только где его меньше, чем `bags`; нет карточки = 0. */
  short: Record<string, number>;
}

/** GET /loader/orders/{id}/dispatch-sources/: спрашивать ли склад и из каких выбирать. */
export interface DispatchSources {
  choose: boolean;
  /** Активные склады в порядке справочника. */
  warehouses: DispatchWarehouse[];
  products: DispatchSourceProduct[];
}

/** Строка `sources` в POST /loader/orders/{id}/dispatch/. */
export interface DispatchSource {
  product: number;
  warehouse: number;
  bags: number;
}

/** Ответы грузчика: id товара → id склада → мешков. */
export type SourceAnswers = Record<number, Record<number, number>>;

/** Отказы отгрузки, после которых опросник проходят заново: сменился состав заказа или склады. */
export const SOURCE_RESTART_CODES: readonly string[] = [
  "sources_required",
  "sources_mismatch",
  "warehouse_inactive",
  "warehouse_not_found",
];

/** Ответы → `sources` для POST: по товару, затем по складу; пустые части не отправляем. */
export function dispatchSourcesPayload(answers: SourceAnswers): DispatchSource[] {
  return Object.entries(answers)
    .flatMap(([product, byWarehouse]) =>
      Object.entries(byWarehouse).map(([warehouse, bags]) => ({
        product: Number(product),
        warehouse: Number(warehouse),
        bags,
      })),
    )
    .filter((source) => source.bags > 0)
    .sort((a, b) => a.product - b.product || a.warehouse - b.warehouse);
}

/**
 * Нехватка остатка — предупреждение, не запрет: «осталось 3 — уйдёт в минус».
 * Остаток показываем только тут; минус на складе — как «осталось 0».
 */
export function shortageNote(product: DispatchSourceProduct, warehouseId: number, bags: number): string {
  const balance = product.short[String(warehouseId)];
  if (balance === undefined || bags <= 0 || bags <= balance) return "";
  return `осталось ${Math.max(balance, 0)} — уйдёт в минус`;
}

/** Откуда товар — теми же словами, что в накладной: «со склада: Мельница» | «Мельница — 12, Мельница 2 — 8». */
export function sourcesText(context: DispatchSources, chosen: Record<number, number>): string {
  const parts = context.warehouses.filter((warehouse) => (chosen[warehouse.id] ?? 0) > 0);
  if (parts.length === 1) return `со склада: ${parts[0].name}`;
  return parts.map((warehouse) => `${warehouse.name} — ${chosen[warehouse.id]}`).join(", ");
}

/**
 * Свой цвет склада в опроснике: грузчик узнаёт склад по цвету, не читая. Цвет
 * привязан к id — не меняется, когда появится новый склад. Красного, зелёного и
 * синего нет: это цвета мешков. На всех белый текст читается (контраст ≥ 4.5).
 */
const WAREHOUSE_TONES = ["#B45309", "#6D28D9", "#BE185D", "#334155"] as const;

export function warehouseTone(warehouseId: number): string {
  return WAREHOUSE_TONES[(Math.max(warehouseId, 1) - 1) % WAREHOUSE_TONES.length];
}

/** Кнопка разбивки под складами. */
export function splitLabel(warehouseCount: number): string {
  return warehouseCount > 2 ? "С нескольких складов…" : "С двух складов…";
}

/** Сколько мешков остаётся последнему складу разбивки (меньше нуля — ввели больше, чем в заказе). */
export function splitRemainder(bags: number, parts: number[]): number {
  return parts.reduce((rest, part) => rest - part, bags);
}

/**
 * Прежние ответы годятся, пока те же товары с теми же мешками и те же склады.
 * Остатки, названия и подписи могли измениться — на ответ это не влияет.
 */
export function sameSourceContext(a: DispatchSources, b: DispatchSources): boolean {
  const key = (context: DispatchSources) =>
    JSON.stringify([
      context.products.map((product) => [product.product, product.bags]),
      context.warehouses.map((warehouse) => warehouse.id),
    ]);
  return key(a) === key(b);
}

/** Все товары разложены по складам ровно на свои мешки — можно отгружать. */
export function answersComplete(context: DispatchSources, answers: SourceAnswers): boolean {
  return context.products.every(
    (product) => Object.values(answers[product.product] ?? {}).reduce((sum, bags) => sum + bags, 0) === product.bags,
  );
}
