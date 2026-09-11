"use client";

import { useEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { grainTripHref } from "@/lib/grain";
import type { GrainWagon } from "@/lib/types";
import { useApi } from "@/lib/use-api";

// Бумажный бланк мельницы Аксу, перенесённый 1:1: координаты сняты с фото
// бланка (--x/--y — миллиметры на пиксель фото), поэтому лист печатается на
// A4 без полей в масштабе 100%. Стили живут только внутри .aksu-waybill.
const WAYBILL_CSS = `
.aksu-waybill-page {
  min-height: 100vh;
  padding: 1px 0 0;
  background: #e7e7e7;
  color: #111;
  font-family: Arial, Helvetica, sans-serif;
}
.aksu-waybill-message { max-width: 210mm; margin: 24px auto; padding: 0 12px; font-size: 14px; }
.aksu-waybill {
  --x: 0.26119403mm;
  --y: 0.27197802mm;
  --ink: #111;
  --thin: 0.54mm;
  --thick: 0.8mm;
  --page-scale: 1;
  color: var(--ink);
}
.aksu-waybill * { box-sizing: border-box; }
.aksu-waybill button { font: inherit; }
.aksu-waybill .toolbar {
  max-width: 210mm;
  margin: 18px auto 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  font-size: 13px;
  line-height: 1.5;
  color: #454545;
}
.aksu-waybill .toolbar-title { display: block; font-weight: 700; color: #111; }
.aksu-waybill .toolbar-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.aksu-waybill .toolbar a { color: #111; }
.aksu-waybill .toolbar button {
  border: 1px solid #aaa;
  border-radius: 4px;
  padding: 8px 13px;
  cursor: pointer;
  background: #fff;
  color: #111;
}
.aksu-waybill .toolbar .print-button { background: #222; border-color: #222; color: #fff; }
.aksu-waybill .toolbar button:focus-visible { outline: 2px solid #555; outline-offset: 3px; }
.aksu-waybill .page-frame {
  width: calc(210mm * var(--page-scale));
  height: calc(297mm * var(--page-scale));
  margin: 0 auto 24px;
}
.aksu-waybill .sheet {
  position: relative;
  width: 210mm;
  height: 297mm;
  background: #fff;
  transform: scale(var(--page-scale));
  transform-origin: top left;
  box-shadow: 0 2px 16px #0002;
  font-size: calc(20 * var(--x));
  line-height: 1.2;
}
.aksu-waybill .sheet h1 { margin: 0; }
.aksu-waybill .doc-title {
  position: absolute;
  left: calc(72 * var(--x));
  top: calc(76 * var(--y));
  font-size: calc(18.5 * var(--x));
  line-height: calc(24 * var(--y));
  font-weight: 700;
  white-space: nowrap;
}
.aksu-waybill .mill {
  position: absolute;
  left: calc(410 * var(--x));
  top: calc(68 * var(--y));
  font-size: calc(22 * var(--x));
  line-height: calc(28 * var(--y));
  white-space: nowrap;
}
.aksu-waybill .header-label {
  position: absolute;
  left: calc(20 * var(--x));
  font-size: calc(20 * var(--x));
  line-height: calc(24 * var(--y));
  font-weight: 700;
  white-space: nowrap;
}
.aksu-waybill .number-label { top: calc(98 * var(--y)); }
.aksu-waybill .date-label { top: calc(120 * var(--y)); }
.aksu-waybill .vehicle-label {
  top: calc(162 * var(--y));
  font-size: calc(22 * var(--x));
  line-height: calc(27 * var(--y));
}
.aksu-waybill .buyer-label { top: calc(196 * var(--y)); font-size: calc(18.5 * var(--x)); }
.aksu-waybill .header-field {
  display: block;
  position: absolute;
  overflow: hidden;
  white-space: nowrap;
  font-size: calc(21 * var(--x));
  font-weight: 400;
  line-height: calc(25 * var(--y));
  text-align: center;
  padding: 0 calc(4 * var(--x));
  border-bottom: var(--thin) solid var(--ink);
}
.aksu-waybill .dashed { border-bottom-style: dashed; }
.aksu-waybill .number-field {
  left: calc(39 * var(--x)); top: calc(92 * var(--y));
  width: calc(192 * var(--x)); height: calc(20 * var(--y));
  font-size: calc(19 * var(--x)); line-height: calc(19 * var(--y));
}
.aksu-waybill .date-field {
  left: calc(77 * var(--x)); top: calc(110 * var(--y));
  width: calc(323 * var(--x)); height: calc(24 * var(--y));
}
.aksu-waybill .vehicle-field {
  left: calc(187 * var(--x)); top: calc(148 * var(--y));
  width: calc(362 * var(--x)); height: calc(27 * var(--y));
}
.aksu-waybill .buyer-field {
  left: calc(132 * var(--x)); top: calc(187 * var(--y));
  width: calc(597 * var(--x)); height: calc(29 * var(--y));
  text-align: left;
}
.aksu-waybill .goods {
  position: absolute;
  left: calc(15 * var(--x));
  top: calc(244 * var(--y));
  width: calc(722 * var(--x));
  border-collapse: collapse;
  table-layout: fixed;
  border: 0;
  font-size: calc(20 * var(--x));
}
.aksu-waybill .goods th, .aksu-waybill .goods tbody td {
  border: var(--thin) solid var(--ink);
  padding: 0;
  text-align: center;
}
.aksu-waybill .goods th {
  height: calc(46 * var(--y));
  font-weight: 700;
  vertical-align: top;
  line-height: calc(21.5 * var(--y));
  border-top-width: var(--thick);
  border-bottom-width: var(--thick);
}
.aksu-waybill .goods th:first-child, .aksu-waybill .goods tbody td:first-child { border-left-width: var(--thick); }
.aksu-waybill .goods th:last-child, .aksu-waybill .goods tbody td:last-child { border-right-width: var(--thick); }
.aksu-waybill .goods tbody tr:last-child td { border-bottom-width: var(--thick); }
.aksu-waybill .goods th:nth-child(1) {
  vertical-align: bottom;
  font-size: calc(19 * var(--x));
  white-space: nowrap;
  text-align: left;
  padding-left: calc(2 * var(--x));
}
.aksu-waybill .goods th:nth-child(2) { text-align: left; padding-left: calc(7 * var(--x)); }
.aksu-waybill .goods th:nth-child(4) { font-size: calc(19 * var(--x)); }
.aksu-waybill .goods th:nth-child(7) { text-align: left; padding-left: calc(3 * var(--x)); }
.aksu-waybill .cost-second-line { padding-left: calc(13 * var(--x)); }
.aksu-waybill .goods tbody td {
  vertical-align: middle;
  overflow: hidden;
  white-space: nowrap;
  line-height: 1.15;
}
.aksu-waybill .goods .goods-row-1 { height: calc(42 * var(--y)); }
.aksu-waybill .goods .goods-row-2 { height: calc(69 * var(--y)); }
.aksu-waybill .goods .goods-row-3 { height: calc(45 * var(--y)); }
.aksu-waybill .goods .goods-row-4, .aksu-waybill .goods .goods-row-5, .aksu-waybill .goods .goods-row-6 {
  height: calc(46 * var(--y));
}
.aksu-waybill .goods .row-number {
  vertical-align: bottom;
  text-align: right;
  padding: 0 calc(3 * var(--x)) calc(3 * var(--y)) 0;
  font-size: calc(19 * var(--x));
  font-weight: 700;
}
.aksu-waybill .goods .item-name { text-align: left; padding-left: calc(2 * var(--x)); }
.aksu-waybill .goods .primary-item { font-size: calc(32 * var(--x)); font-weight: 400; }
.aksu-waybill .goods .numeric-cell { padding-inline: calc(3 * var(--x)); }
.aksu-waybill .goods tfoot tr { height: calc(34 * var(--y)); }
.aksu-waybill .summary-spacer { padding: 0; border: 0; }
.aksu-waybill .total-cell { padding: 0; border: var(--thick) solid var(--ink); }
.aksu-waybill .total-content { position: relative; height: calc(32 * var(--y)); }
.aksu-waybill .total-label {
  position: absolute;
  left: calc(20 * var(--x));
  top: calc(7 * var(--y));
  font-weight: 700;
  font-size: calc(20 * var(--x));
}
.aksu-waybill .total-value {
  position: absolute;
  left: calc(127 * var(--x));
  top: calc(6 * var(--y));
  width: calc(204 * var(--x));
  height: calc(21 * var(--y));
  line-height: calc(21 * var(--y));
  border-bottom: 0.36mm solid var(--ink);
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
}
.aksu-waybill .approval {
  position: absolute;
  left: 0; right: 0;
  height: calc(64 * var(--y));
  font-size: calc(19 * var(--x));
  line-height: calc(24 * var(--y));
  font-weight: 700;
}
.aksu-waybill .director { top: calc(682 * var(--y)); }
.aksu-waybill .warehouse { top: calc(756 * var(--y)); }
.aksu-waybill .customer { top: calc(834 * var(--y)); }
.aksu-waybill .cashier { top: calc(907 * var(--y)); }
.aksu-waybill .security { top: calc(980 * var(--y)); }
.aksu-waybill .approval-role { position: absolute; left: calc(72 * var(--x)); top: 0; white-space: nowrap; }
.aksu-waybill .signature-rule {
  position: absolute;
  left: calc(238 * var(--x));
  top: calc(20 * var(--y));
  width: calc(194 * var(--x));
  border-bottom: 0.36mm solid var(--ink);
}
.aksu-waybill .signature-caption {
  position: absolute;
  left: calc(160 * var(--x));
  top: calc(35 * var(--y));
  white-space: nowrap;
}
.aksu-waybill .person-name {
  position: absolute;
  left: calc(540 * var(--x));
  top: 0;
  min-width: calc(147 * var(--x));
  max-width: calc(218 * var(--x));
  min-height: calc(24 * var(--y));
  white-space: nowrap;
  overflow: hidden;
}
.aksu-waybill .empty-name {
  left: calc(542 * var(--x));
  width: calc(145 * var(--x));
  min-height: calc(20 * var(--y));
  height: calc(20 * var(--y));
  line-height: calc(19 * var(--y));
  border-bottom: 0.36mm solid var(--ink);
}
.aksu-waybill .stamp-placeholder {
  position: absolute;
  left: calc(451 * var(--x));
  top: calc(931 * var(--y));
  width: calc(90 * var(--x));
  height: calc(38 * var(--y));
  display: flex;
  align-items: center;
  justify-content: center;
  border: var(--thin) solid var(--ink);
  font-weight: 700;
  font-size: calc(20 * var(--x));
}
.aksu-waybill [contenteditable="true"] { outline: none; cursor: text; }
@media screen {
  .aksu-waybill [contenteditable="true"]:focus { background: #f2f2f2; box-shadow: inset 0 0 0 1px #aaa; }
}
@media screen and (max-width: 850px) {
  .aksu-waybill .toolbar { margin: 12px; gap: 10px; }
  .aksu-waybill .toolbar-actions { gap: 5px; }
  .aksu-waybill .toolbar button { padding: 7px 10px; }
  .aksu-waybill .toolbar-hint { max-width: 260px; display: block; }
}
@media screen and (max-width: 520px) {
  .aksu-waybill .toolbar { align-items: flex-start; }
  .aksu-waybill .toolbar-actions { flex-direction: column-reverse; align-items: stretch; }
  .aksu-waybill .toolbar-hint { font-size: 12px; }
}
@page { size: A4 portrait; margin: 0; }
@media print {
  html, body { width: 210mm; margin: 0 !important; padding: 0 !important; background: #fff !important; }
  body > :not(.aksu-waybill-page) { display: none !important; }
  .aksu-waybill-page { min-height: 0; padding: 0; background: #fff; }
  .aksu-waybill .toolbar { display: none !important; }
  .aksu-waybill .page-frame { width: 210mm; height: 297mm; margin: 0 !important; }
  .aksu-waybill .sheet {
    width: 210mm; height: 297mm;
    margin: 0; padding: 0; transform: none !important;
    box-shadow: none; break-inside: avoid; page-break-inside: avoid;
    print-color-adjust: exact; -webkit-print-color-adjust: exact;
  }
  .aksu-waybill [contenteditable="true"] {
    outline: none !important; box-shadow: none !important; background: transparent !important;
  }
}
`;

const PLANT_DATE = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Asia/Almaty",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});
const KG = new Intl.NumberFormat("ru-RU");
const MONEY = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const COLUMN_WIDTHS = ["7.34072%", "22.99169%", "13.98892%", "15.51247%", "12.04986%", "8.31025%", "19.80609%"];
const NUMERIC_COLUMNS = ["Пустой груз", "Груженый груз", "Масса нетто", "Цена за кг", "Стоимость"];
const EXTRA_ROWS = [2, 3, 4, 5, 6];
const SIGNATURES = [
  { position: "director", role: "Директор:", label: "ФИО директора", name: "Егамбердиева Д" },
  { position: "warehouse", role: "Зав Склад:", label: "ФИО заведующего складом", name: "ТАЖИ АРМАН" },
  { position: "customer", role: "Покупатель:", label: "ФИО покупателя", name: "" },
  { position: "cashier", role: "Кассир:", label: "ФИО кассира", name: "Ибрагимова Г" },
  { position: "security", role: "Охрана:", label: "ФИО сотрудника охраны", name: "" },
];

function kg(value: number | null) {
  return value == null ? "" : KG.format(value);
}

function parsePrice(text: string): number | null {
  const compact = text.replace(/\s/g, "").replace(",", ".");
  if (!compact) return null;
  const value = Number(compact);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function keepSingleLine(event: KeyboardEvent<HTMLElement>) {
  if (event.key === "Enter") event.preventDefault();
}

/** Вставка только простого текста: чужое форматирование не ломает бланк. */
function pastePlainText(event: ClipboardEvent<HTMLElement>) {
  event.preventDefault();
  const field = event.currentTarget;
  const text = event.clipboardData.getData("text/plain").replace(/[\r\n\t]+/g, " ");
  const selection = window.getSelection();
  if (!selection) return;
  let range: Range;
  if (selection.rangeCount && field.contains(selection.getRangeAt(0).commonAncestorContainer)) {
    range = selection.getRangeAt(0);
  } else {
    range = document.createRange();
    range.selectNodeContents(field);
    range.collapse(false);
  }
  range.deleteContents();
  const node = document.createTextNode(text);
  range.insertNode(node);
  range.setStartAfter(node);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
  field.dispatchEvent(new Event("input", { bubbles: true }));
}

/** Поле правится прямо на листе, как в бумажном шаблоне. */
function editable(label: string) {
  return {
    contentEditable: true,
    suppressContentEditableWarning: true,
    spellCheck: false,
    role: "textbox" as const,
    "aria-label": label,
    tabIndex: 0,
    onKeyDown: keepSingleLine,
    onPaste: pastePlainText,
  };
}

function WaybillSheet({ trip }: { trip: GrainWagon }) {
  const [price, setPrice] = useState<number | null>(null);
  const net = trip.net_weight_kg;
  const cost = price === null || net == null ? "" : MONEY.format(Math.round(net * price * 100) / 100);
  return (
    <section className="sheet" aria-label="Накладная на отпуск товаров">
      <h1 className="doc-title">Накладная на отпуск товаров</h1>
      <div className="mill">мельница Аксу</div>

      <span className="header-label number-label">N</span>
      <span className="header-field number-field dashed" {...editable("Номер накладной")}>
        {trip.id}
      </span>
      <span className="header-label date-label">ДАТА</span>
      <span className="header-field date-field dashed" {...editable("Дата накладной")}>
        {trip.exited_at ? PLANT_DATE.format(new Date(trip.exited_at)) : ""}
      </span>
      <span className="header-label vehicle-label">№ Автомаши Х</span>
      <span className="header-field vehicle-field dashed" {...editable("Номер автомашины")}>
        {trip.number}
      </span>
      <span className="header-label buyer-label">Покупатель:</span>
      <span className="header-field buyer-field" {...editable("Покупатель")} />

      <table className="goods" aria-label="Товары">
        <colgroup>
          {COLUMN_WIDTHS.map((width) => (
            <col key={width} style={{ width }} />
          ))}
        </colgroup>
        <thead>
          <tr>
            <th scope="col">
              <span>№п/п</span>
            </th>
            <th scope="col">
              Наимено
              <br />
              вание
            </th>
            <th scope="col">
              Пустой
              <br />
              груз
            </th>
            <th scope="col">
              Гружен/й
              <br />
              груз
            </th>
            <th scope="col">
              Масса
              <br />
              нетто
            </th>
            <th scope="col">
              Цена
              <br />
              за кг
            </th>
            <th scope="col">
              Стоимо
              <br />
              <span className="cost-second-line">сть в</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr className="goods-row goods-row-1">
            <td className="row-number">1</td>
            <td className="item-name primary-item" {...editable("Наименование, строка 1")}>
              КЕБЕК
            </td>
            <td className="numeric-cell" {...editable("Пустой груз, строка 1")}>
              {kg(trip.entry_weight_kg)}
            </td>
            <td className="numeric-cell" {...editable("Груженый груз, строка 1")}>
              {kg(trip.exit_weight_kg)}
            </td>
            <td className="numeric-cell" {...editable("Масса нетто, строка 1")}>
              {kg(net)}
            </td>
            <td
              className="numeric-cell"
              {...editable("Цена за кг, строка 1")}
              onInput={(event) => setPrice(parsePrice(event.currentTarget.textContent ?? ""))}
            />
            {/* Сумма пересчитывается от цены, но остаётся правимой вручную. */}
            <td key={`cost:${cost}`} className="numeric-cell" {...editable("Стоимость, строка 1")}>
              {cost}
            </td>
          </tr>
          {EXTRA_ROWS.map((row) => (
            <tr key={row} className={`goods-row goods-row-${row}`}>
              <td className="row-number">{row}</td>
              <td className="item-name" {...editable(`Наименование, строка ${row}`)} />
              {NUMERIC_COLUMNS.map((column) => (
                <td key={column} className="numeric-cell" {...editable(`${column}, строка ${row}`)} />
              ))}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td className="summary-spacer" colSpan={3} aria-hidden="true" />
            <td className="total-cell" colSpan={4}>
              <div className="total-content">
                <span className="total-label">ИТОГО:</span>
                <span key={`total:${cost}`} className="total-value" {...editable("Итого")}>
                  {cost}
                </span>
              </div>
            </td>
          </tr>
        </tfoot>
      </table>

      {SIGNATURES.map((signature) => (
        <div key={signature.position} className={`approval ${signature.position}`}>
          <span className="approval-role">{signature.role}</span>
          <span className="signature-rule" aria-label="Место для подписи" />
          <span className="signature-caption">подпись</span>
          <span className={signature.name ? "person-name" : "person-name empty-name"} {...editable(signature.label)}>
            {signature.name}
          </span>
        </div>
      ))}
      <div className="stamp-placeholder" aria-label="Место печати">
        М.П.
      </div>
    </section>
  );
}

/** Накладная на отпуск товаров мельницы Аксу по завершённому вывозу. */
export function PassageWaybill({ trip }: { trip: GrainWagon }) {
  const root = useRef<HTMLDivElement>(null);
  const [version, setVersion] = useState(0);
  // На узком экране лист уменьшается целиком; печать остаётся A4 100%.
  useEffect(() => {
    function fit() {
      const fullWidth = (210 * 96) / 25.4;
      const scale = Math.max(0.1, Math.min(1, (document.documentElement.clientWidth - 24) / fullWidth));
      root.current?.style.setProperty("--page-scale", String(scale));
    }
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);
  return (
    <div className="aksu-waybill" ref={root}>
      <div className="toolbar" role="group" aria-label="Управление накладной">
        <div>
          <span className="toolbar-title">Накладная на отпуск товаров · A4</span>
          <span className="toolbar-hint">
            Поля правятся прямо на листе. Печать: A4, масштаб 100%, без колонтитулов.
          </span>
        </div>
        <div className="toolbar-actions">
          <a href={grainTripHref(trip)}>К рейсу</a>
          <button
            type="button"
            onClick={() => {
              if (window.confirm("Вернуть данные рейса и убрать правки?")) setVersion((value) => value + 1);
            }}
          >
            Сбросить правки
          </button>
          <button type="button" className="print-button" onClick={() => window.print()}>
            Печать
          </button>
        </div>
      </div>
      <div className="page-frame">
        <WaybillSheet key={version} trip={trip} />
      </div>
    </div>
  );
}

export function PassageWaybillPage({ tripId }: { tripId: number }) {
  const valid = Number.isSafeInteger(tripId) && tripId > 0;
  const detail = useApi<GrainWagon>(valid ? `/grain/passages/${tripId}/` : null);
  const failure = detail.error || (detail.errorStatus ? "Накладная недоступна. Проверьте права доступа." : "");
  const trip = detail.data;
  return (
    <main className="aksu-waybill-page">
      <style>{WAYBILL_CSS}</style>
      {!valid ? (
        <div className="aksu-waybill-message">
          <ErrorAlert message="Неверный номер рейса." />
        </div>
      ) : failure ? (
        <div className="aksu-waybill-message">
          <ErrorAlert message={failure} onRetry={() => void detail.reload()} />
        </div>
      ) : !trip ? (
        <div className="aksu-waybill-message">
          <DataGate loading={detail.loading} />
        </div>
      ) : trip.direction !== "passage" || trip.status !== "completed" ? (
        <div className="aksu-waybill-message">
          <p>Накладная доступна после завершения вывоза.</p>
        </div>
      ) : (
        <PassageWaybill key={trip.id} trip={trip} />
      )}
    </main>
  );
}
