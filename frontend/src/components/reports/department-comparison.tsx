"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { formatCurrency } from "@/lib/utils";
import type { DepartmentReport } from "@/lib/types";

export function DepartmentComparison({
  rows,
  incomeOnly = false,
  from,
  to,
}: {
  rows: DepartmentReport[];
  incomeOnly?: boolean;
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
  const metric = incomeOnly ? "net_by_currency" : "sales_by_currency";
  const sorted = [...rows].sort((a, b) => Number(b[metric]?.[currency] || 0) - Number(a[metric]?.[currency] || 0));
  const leading = Number(sorted[0]?.[metric]?.[currency] || 0);
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
      ["Отдел", "Валюта", ...(!incomeOnly ? ["Отгружено"] : []), "Поступило", "Возвращено", "Чистое поступление"],
      ...rows.flatMap((row) =>
        currencies.map((unit) => [
          row.name,
          unit,
          ...(!incomeOnly ? [row.sales_by_currency?.[unit] || "0"] : []),
          row.received_by_currency[unit] || "0",
          row.refunded_by_currency[unit] || "0",
          row.net_by_currency[unit] || "0",
        ]),
      ),
    ];
    const url = URL.createObjectURL(
      new Blob(["\uFEFF", lines.map((line) => line.map(cell).join(";")).join("\r\n")], {
        type: "text/csv;charset=utf-8",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `departments-${from || "all"}-${to || "current"}.csv`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3">
        <div>
          <CardTitle>{incomeOnly ? "Поступления по отделам" : "Продажи по отделам"}</CardTitle>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            {incomeOnly
              ? "Оплаты по дате подтверждения, возвраты — по дате завершения."
              : "Продажи по дате отгрузки. Оплаты и возвраты — по дате операции."}
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
                {!incomeOnly && <TH className="text-right">Отгружено</TH>}
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
                      <span className="size-2 rounded-full" style={{ backgroundColor: row.color }} />
                      {row.name}
                    </span>
                    {leading > 0 && Number(row[metric]?.[currency] || 0) === leading && (
                      <span className="mt-1 block text-xs text-[var(--success)]">
                        {incomeOnly ? "Больше поступлений" : "Больше продаж"} · {currency}
                      </span>
                    )}
                  </TD>
                  {!incomeOnly && (
                    <TD className="text-right font-semibold tabular-nums">
                      {formatCurrency(row.sales_by_currency?.[currency] || "0", currency)}
                    </TD>
                  )}
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
