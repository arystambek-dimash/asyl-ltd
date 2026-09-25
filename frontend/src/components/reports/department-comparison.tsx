"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DepartmentDot } from "@/components/ui/department-badge";
import { Select } from "@/components/ui/select";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { downloadBlob } from "@/lib/download";
import { formatCurrency } from "@/lib/utils";
import type { DepartmentReport } from "@/lib/types";

export function DepartmentComparison({
  rows,
  from,
  to,
}: {
  rows: DepartmentReport[];
  from?: string | null;
  to?: string | null;
}) {
  const [selectedCurrency, setSelectedCurrency] = useState("KZT");
  const currencies = [
    ...new Set(
      rows.flatMap((row) => [...Object.keys(row.sales_by_currency ?? {}), ...Object.keys(row.net_by_currency)]),
    ),
  ].sort();
  const currency = currencies.includes(selectedCurrency) ? selectedCurrency : currencies[0] || "KZT";
  const sales = (row: DepartmentReport) => Number(row.sales_by_currency?.[currency] || 0);
  const sorted = [...rows].sort((a, b) => sales(b) - sales(a));
  const leading = sorted[0] ? sales(sorted[0]) : 0;
  function download() {
    const cell = (value: string | number) => {
      const raw = String(value);
      // Keep negative net amounts numeric, while escaping spreadsheet formulas
      // in user-controlled department names (including leading whitespace).
      const safe = !/^-?\d+(\.\d+)?$/.test(raw) && /^[\s]*[=+@\-\t\r\n]/.test(raw) ? `'${raw}` : raw;
      return `"${safe.replaceAll('"', '""')}"`;
    };
    const lines: (string | number)[][] = [
      ["Период", from || "С начала учёта", to || "По текущую дату"],
      ["Отдел", "Валюта", "Отгружено", "Поступило", "Возвращено", "Чистое поступление"],
      ...rows.flatMap((row) =>
        currencies.map((unit) => [
          row.name,
          unit,
          row.sales_by_currency?.[unit] || "0",
          row.received_by_currency[unit] || "0",
          row.refunded_by_currency[unit] || "0",
          row.net_by_currency[unit] || "0",
        ]),
      ),
    ];
    downloadBlob(
      new Blob(["\uFEFF", lines.map((line) => line.map(cell).join(";")).join("\r\n")], {
        type: "text/csv;charset=utf-8",
      }),
      `departments-${from || "all"}-${to || "current"}.csv`,
    );
  }
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3">
        <div>
          <CardTitle>Продажи по отделам</CardTitle>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            Продажи по дате отгрузки. Оплаты и возвраты — по дате операции.
          </p>
        </div>
        <div className="flex gap-2">
          {currencies.length > 1 && (
            <Select
              aria-label="Валюта сравнения отделов"
              value={currency}
              onChange={(event) => setSelectedCurrency(event.target.value)}
            >
              {currencies.map((unit) => (
                <option key={unit}>{unit}</option>
              ))}
            </Select>
          )}
          <Button size="sm" variant="outline" disabled={!rows.length} onClick={download}>
            Скачать CSV
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {!rows.length ? (
          <p className="text-sm text-[var(--muted-foreground)]">За этот период операций нет.</p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Отдел</TH>
                <TH className="text-right">Отгружено</TH>
                <TH className="text-right">Поступило</TH>
                <TH className="text-right">Возвращено</TH>
                <TH className="text-right">Чистое поступление</TH>
              </TR>
            </THead>
            <TBody>
              {sorted.map((row) => (
                <TR key={row.code}>
                  <TD>
                    <span className="inline-flex items-center gap-2 font-medium">
                      <DepartmentDot color={row.color} className="size-2" />
                      {row.name}
                    </span>
                    {leading > 0 && sales(row) === leading && (
                      <span className="mt-1 block text-xs text-[var(--success)]">Больше продаж · {currency}</span>
                    )}
                  </TD>
                  <TD className="text-right font-semibold tabular-nums">
                    {formatCurrency(row.sales_by_currency?.[currency] || "0", currency)}
                  </TD>
                  <TD className="text-right tabular-nums">
                    {formatCurrency(row.received_by_currency[currency] || "0", currency)}
                  </TD>
                  <TD className="text-right tabular-nums">
                    {formatCurrency(row.refunded_by_currency[currency] || "0", currency)}
                  </TD>
                  <TD className="text-right font-medium tabular-nums">
                    {formatCurrency(row.net_by_currency[currency] || "0", currency)}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
