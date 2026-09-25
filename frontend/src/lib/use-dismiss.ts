"use client";
import { useEffect, useRef, type RefObject } from "react";

type ElementRef = RefObject<HTMLElement | null>;

type DismissOptions = {
  /** Клик по этим элементам (обычно триггер вне поповера) не закрывает. */
  ignoreRefs?: readonly ElementRef[];
  /** Куда вернуть фокус после Escape, если он был внутри поповера. */
  returnFocusRef?: ElementRef;
};

/** Закрытие поповера по клику/тапу мимо и по Escape. */
export function useDismiss(
  ref: ElementRef,
  onClose: () => void,
  active: boolean,
  { ignoreRefs = [], returnFocusRef }: DismissOptions = {},
) {
  const onCloseRef = useRef(onClose);
  const ignoreRefsRef = useRef(ignoreRefs);
  const returnFocusRefRef = useRef(returnFocusRef);

  useEffect(() => {
    onCloseRef.current = onClose;
    ignoreRefsRef.current = ignoreRefs;
    returnFocusRefRef.current = returnFocusRef;
  }, [ignoreRefs, onClose, returnFocusRef]);

  useEffect(() => {
    if (!active) return;
    const inside = (target: Node | null) =>
      Boolean(ref.current?.contains(target)) ||
      ignoreRefsRef.current.some((ignoredRef) => ignoredRef.current?.contains(target));
    const onDown = (event: MouseEvent | TouchEvent) => {
      if (!ref.current || inside(event.target as Node)) return;
      onCloseRef.current();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      const returnFocusTo = inside(document.activeElement) ? returnFocusRefRef.current?.current : null;
      onCloseRef.current();
      if (!returnFocusTo) return;
      event.preventDefault();
      returnFocusTo.focus();
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
