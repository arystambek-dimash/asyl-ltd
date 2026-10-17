"use client";
import { useEffect, useRef, useState } from "react";

const carriesFiles = (event: DragEvent) => Boolean(event.dataTransfer?.types.includes("Files"));

/**
 * Файлы, брошенные в любое место окна, пока компонент на экране. Промах мимо
 * зоны не открывает и не скачивает файл браузером — бросок засчитывается везде.
 * Возвращает, тащат ли файл над окном, чтобы подсветить зону.
 */
export function useFileDrop(onDrop: (files: File[]) => void): boolean {
  const onDropRef = useRef(onDrop);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    onDropRef.current = onDrop;
  }, [onDrop]);

  useEffect(() => {
    // dragenter/dragleave приходят на каждый вложенный элемент: считаем глубину.
    let depth = 0;
    const onEnter = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      depth += 1;
      setDragging(true);
    };
    const onOver = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    };
    const onLeave = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDragging(false);
    };
    const onDropEvent = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      depth = 0;
      setDragging(false);
      const files = Array.from(event.dataTransfer?.files ?? []);
      if (files.length) onDropRef.current(files);
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDropEvent);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDropEvent);
    };
  }, []);

  return dragging;
}
