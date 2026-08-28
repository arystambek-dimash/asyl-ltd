"use client";

import { useId, useState } from "react";
import { FileVideo, UploadCloud, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { MODEL_TEST_ACCEPT, formatBytes, validateModelTestFile } from "@/lib/model-tests";
import { cn } from "@/lib/utils";

export function VideoDropZone({
  file,
  maxBytes,
  disabled,
  onFile,
  onReject,
}: {
  file: File | null;
  maxBytes: number;
  disabled?: boolean;
  onFile: (file: File | null) => void;
  onReject: (message: string) => void;
}) {
  const inputId = useId();
  const [dragging, setDragging] = useState(false);

  const choose = (next: File | undefined) => {
    if (!next || disabled) return;
    const validationError = validateModelTestFile(next, maxBytes);
    if (validationError) {
      onReject(validationError);
      return;
    }
    onReject("");
    onFile(next);
  };

  if (file) {
    return (
      <div className="flex items-center gap-3 rounded-lg border bg-[var(--card)] px-3 py-2.5">
        <FileVideo className="size-5 shrink-0 text-[var(--ring)]" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{file.name}</div>
          <div className="text-xs text-[var(--muted-foreground)]">{formatBytes(file.size)}</div>
        </div>
        <Button variant="ghost" size="icon" disabled={disabled} aria-label="Убрать видео" onClick={() => onFile(null)}>
          <X className="size-4" />
        </Button>
      </div>
    );
  }

  return (
    <div>
      <label
        htmlFor={inputId}
        onDragEnter={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          choose(event.dataTransfer.files[0]);
        }}
        className={cn(
          "flex min-h-44 cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-5 py-8 text-center transition-colors",
          dragging
            ? "border-[var(--ring)] bg-[var(--ring)]/10"
            : "border-[var(--border)] bg-[var(--muted)]/25 hover:border-[var(--ring)]/60",
          disabled && "cursor-not-allowed opacity-60",
        )}
      >
        <span className="flex size-12 items-center justify-center rounded-full bg-[var(--ring)]/10 text-[var(--ring)]">
          <UploadCloud className="size-6" />
        </span>
        <span className="font-medium">Перетащите видео сюда или нажмите для выбора</span>
        <span className="text-xs text-[var(--muted-foreground)]">
          MP4, MOV, AVI или MKV · до {formatBytes(maxBytes)}
        </span>
        <input
          id={inputId}
          className="sr-only"
          type="file"
          accept={MODEL_TEST_ACCEPT}
          disabled={disabled}
          onChange={(event) => {
            choose(event.currentTarget.files?.[0]);
            event.currentTarget.value = "";
          }}
        />
      </label>
    </div>
  );
}
