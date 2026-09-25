import { afterEach, describe, expect, it, vi } from "vitest";
import { focusableElements, nextRovingIndex, restoreFocus, trapTab } from "@/lib/focus";

function tabEvent(shiftKey = false) {
  return { key: "Tab", shiftKey, preventDefault: vi.fn() };
}

function mount(html: string) {
  const root = document.createElement("div");
  root.innerHTML = html;
  document.body.append(root);
  return root;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("focusableElements", () => {
  it("skips disabled, hidden, negative tabindex and inert elements", () => {
    const root = mount(`
      <button>Первая</button>
      <button disabled>Выключена</button>
      <input aria-label="Поле" />
      <button hidden>Скрыта</button>
      <div tabindex="-1">Не в порядке Tab</div>
      <div inert><button>Инертна</button></div>
      <a href="/orders">Ссылка</a>
    `);

    // jsdom отдаёт совпадения по порядку селекторов, а не документа — сверяем состав.
    const names = focusableElements(root).map((element) => element.textContent || element.getAttribute("aria-label"));
    expect(new Set(names)).toEqual(new Set(["Первая", "Поле", "Ссылка"]));
  });
});

describe("trapTab", () => {
  it("wraps Tab and Shift+Tab at the edges and from the container itself", () => {
    const root = mount(`<div tabindex="-1" id="dialog"><button>Первая</button><button>Последняя</button></div>`);
    const dialog = root.querySelector<HTMLElement>("#dialog")!;
    const [first, last] = Array.from(dialog.querySelectorAll("button"));

    last.focus();
    const forward = tabEvent();
    trapTab(forward, dialog);
    expect(forward.preventDefault).toHaveBeenCalled();
    expect(first).toHaveFocus();

    dialog.focus();
    trapTab(tabEvent(true), dialog);
    expect(last).toHaveFocus();

    const middle = tabEvent(true);
    trapTab(middle, dialog);
    expect(middle.preventDefault).not.toHaveBeenCalled();
  });

  it("pulls focus back from outside and holds the container when nothing is focusable", () => {
    const root = mount(`<button id="outside">Снаружи</button><div tabindex="-1" id="dialog"><p>Текст</p></div>`);
    const outside = root.querySelector<HTMLElement>("#outside")!;
    const dialog = root.querySelector<HTMLElement>("#dialog")!;

    outside.focus();
    const event = tabEvent();
    trapTab(event, dialog);
    expect(event.preventDefault).toHaveBeenCalled();
    expect(dialog).toHaveFocus();

    const other = { key: "Enter", shiftKey: false, preventDefault: vi.fn() };
    trapTab(other, dialog);
    expect(other.preventDefault).not.toHaveBeenCalled();
  });
});

describe("restoreFocus", () => {
  it("focuses only a connected, enabled and non-inert target", () => {
    const root = mount(
      `<button id="ok">Ок</button><button id="off" disabled>Выкл</button><div inert><button id="inert">Инерт</button></div>`,
    );
    const ok = root.querySelector<HTMLElement>("#ok")!;

    restoreFocus(root.querySelector<HTMLElement>("#off"));
    restoreFocus(root.querySelector<HTMLElement>("#inert"));
    expect(document.body).toHaveFocus();

    const detached = document.createElement("button");
    restoreFocus(detached);
    expect(document.body).toHaveFocus();

    restoreFocus(ok);
    expect(ok).toHaveFocus();
  });
});

describe("nextRovingIndex", () => {
  it("moves by arrows with wrap-around and jumps with Home/End", () => {
    expect(nextRovingIndex("ArrowDown", 2, 3)).toBe(0);
    expect(nextRovingIndex("ArrowUp", 0, 3)).toBe(2);
    expect(nextRovingIndex("ArrowDown", -1, 3)).toBe(0);
    expect(nextRovingIndex("Home", 1, 3)).toBe(0);
    expect(nextRovingIndex("End", 0, 3)).toBe(2);
    expect(nextRovingIndex("ArrowRight", 0, 3)).toBeNull();
  });

  it("uses left/right for horizontal lists and ignores empty lists", () => {
    expect(nextRovingIndex("ArrowRight", 2, 3, "horizontal")).toBe(0);
    expect(nextRovingIndex("ArrowLeft", 0, 3, "horizontal")).toBe(2);
    expect(nextRovingIndex("ArrowDown", 0, 3, "horizontal")).toBeNull();
    expect(nextRovingIndex("Home", 0, 0)).toBeNull();
  });
});
