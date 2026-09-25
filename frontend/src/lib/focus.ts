/** Общая клавиатурная механика диалогов и поповеров: ловушка Tab, возврат
 * фокуса и перемещение по спискам стрелками/Home/End. */

const FOCUSABLE_SELECTOR = [
  "button:not(:disabled)",
  "a[href]",
  "input:not(:disabled)",
  "select:not(:disabled)",
  "textarea:not(:disabled)",
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(",");

/** Элементы, до которых реально доходит Tab: без скрытых и без [inert]. */
export function focusableElements(container: ParentNode): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => element.tabIndex >= 0 && !element.hidden && !element.closest("[inert]"),
  );
}

/** Элемент в фокусе — запоминается перед открытием диалога, чтобы вернуть фокус. */
export function focusedElement(): HTMLElement | null {
  return document.activeElement instanceof HTMLElement ? document.activeElement : null;
}

/** Возвращает фокус, если элемент ещё на странице и доступен. */
export function restoreFocus(target: HTMLElement | null) {
  if (target?.isConnected && !target.matches(":disabled") && !target.closest("[inert]")) target.focus();
}

type TabKeyEvent = Pick<KeyboardEvent, "key" | "shiftKey" | "preventDefault">;

/** Зацикливает Tab/Shift+Tab внутри контейнера, в том числе когда фокус
 * на самом контейнере или ушёл наружу. */
export function trapTab(event: TabKeyEvent, container: HTMLElement) {
  if (event.key !== "Tab") return;
  const focusable = focusableElements(container);
  if (focusable.length === 0) {
    event.preventDefault();
    container.focus();
    return;
  }

  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const focused = document.activeElement;
  const outside = !container.contains(focused);
  if (event.shiftKey && (focused === first || focused === container || outside)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (focused === last || outside)) {
    event.preventDefault();
    first.focus();
  }
}

/** Следующий индекс в списке с «бегущим» фокусом (меню, листбокс, вкладки);
 * null — клавиша не навигационная. */
export function nextRovingIndex(
  key: string,
  index: number,
  length: number,
  orientation: "vertical" | "horizontal" = "vertical",
): number | null {
  if (length === 0) return null;
  const [previousKey, nextKey] = orientation === "vertical" ? ["ArrowUp", "ArrowDown"] : ["ArrowLeft", "ArrowRight"];
  if (key === nextKey) return (index + 1) % length;
  if (key === previousKey) return (index - 1 + length) % length;
  if (key === "Home") return 0;
  if (key === "End") return length - 1;
  return null;
}
