"use client";
import { useEffect, useRef, type RefObject } from "react";

/** Закрытие поповера по клику/тапу мимо и по Escape. */
export function useDismiss(
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
  active: boolean,
  ignoreRefs: readonly RefObject<HTMLElement | null>[] = [],
) {
  const onCloseRef = useRef(onClose);
  const ignoreRefsRef = useRef(ignoreRefs);

  useEffect(() => {
    onCloseRef.current = onClose;
    ignoreRefsRef.current = ignoreRefs;
  }, [ignoreRefs, onClose]);

  useEffect(() => {
    if (!active) return;
    const onDown = (event: MouseEvent | TouchEvent) => {
      const target = event.target as Node;
      if (!ref.current || ref.current.contains(target)) return;
      if (ignoreRefsRef.current.some((ignoredRef) => ignoredRef.current?.contains(target))) return;
      onCloseRef.current();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [active, ref]);
}
