"use client";
import { useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowUp, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { PHONE_INPUT_TEXT, cn } from "@/lib/utils";

const MAX_QUESTION = 2000;

/** Поле вопроса: Enter — отправить, Shift+Enter — новая строка; пока идёт ответ — «Остановить». */
export function Composer({
  busy,
  onAsk,
  onStop,
}: {
  busy: boolean;
  onAsk: (question: string) => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const question = text.trim();

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (busy || !question) return;
    onAsk(question);
    setText("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) submit(event);
  }

  return (
    <form onSubmit={submit} className="flex items-end gap-2">
      <Textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        maxLength={MAX_QUESTION}
        rows={2}
        placeholder="Спросите о заказах, долгах, складе, отгрузках…"
        aria-label="Вопрос ассистенту"
        className={cn("max-h-48 min-h-[44px] resize-none", PHONE_INPUT_TEXT)}
      />
      {busy ? (
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="size-11 shrink-0"
          onClick={onStop}
          aria-label="Остановить"
        >
          <Square />
        </Button>
      ) : (
        <Button type="submit" size="icon" className="size-11 shrink-0" disabled={!question} aria-label="Отправить">
          <ArrowUp />
        </Button>
      )}
    </form>
  );
}
