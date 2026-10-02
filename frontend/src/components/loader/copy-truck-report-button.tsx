"use client";
import { useEffect, useState } from "react";
import { ClipboardCopy, LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/data-state";
import { apiError } from "@/lib/api";
import { copyTextFrom } from "@/lib/clipboard";
import { truckReportText, type HistoryReportScope } from "@/lib/loader";
import { cn } from "@/lib/utils";

/** Сколько держится «Скопировано ✓». */
const COPIED_MS = 2000;
const COPY_REFUSED = "Не удалось скопировать — браузер не дал доступ к буферу обмена";

/**
 * «Скопировать отчёт» у фур: текст для чата отгрузок одним нажатием — сервер
 * его составляет, кнопка кладёт в буфер, грузчик вставляет в WhatsApp. Ботом
 * не отправляется. Запись в буфер начинается в самом нажатии, текст
 * догружается в неё: Safari на iPhone после ожидания ответа в буфер не пускает.
 * Ошибка — под кнопкой, а не молча.
 */
export function CopyTruckReportButton({
  scope,
  label = "Скопировать отчёт",
  disabled = false,
  className,
  buttonClassName,
}: {
  scope: HistoryReportScope;
  label?: string;
  disabled?: boolean;
  className?: string;
  buttonClassName?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);

  async function copy() {
    setBusy(true);
    setCopied(false);
    setError("");
    try {
      // Ни одного await до записи в буфер: запрос текста и запись стартуют в нажатии.
      if (await copyTextFrom(truckReportText(scope))) setCopied(true);
      else setError(COPY_REFUSED);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Button
        variant="outline"
        className={cn("h-11 w-full", buttonClassName)}
        disabled={disabled || busy}
        onClick={() => void copy()}
      >
        {busy ? <LoaderCircle className="size-4 animate-spin" /> : <ClipboardCopy className="size-4" />}
        {copied ? "Скопировано ✓" : label}
      </Button>
      <FormError message={error} className="bg-[var(--card)] text-left" />
    </div>
  );
}
