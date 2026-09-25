import { fireEvent, render } from "@testing-library/react";
import { useRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { useDismiss } from "@/lib/use-dismiss";

function Fixture({
  active = true,
  onClose,
  returnFocus = false,
}: {
  active?: boolean;
  onClose: () => void;
  returnFocus?: boolean;
}) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  useDismiss(popoverRef, onClose, active, {
    ignoreRefs: [triggerRef],
    returnFocusRef: returnFocus ? triggerRef : undefined,
  });

  return (
    <>
      <button ref={triggerRef}>Триггер</button>
      <div ref={popoverRef}>
        <button>Внутри</button>
      </div>
      <button>Снаружи</button>
    </>
  );
}

describe("useDismiss", () => {
  it("closes on an outside click, touch and Escape", () => {
    const onClose = vi.fn();
    const { getByRole } = render(<Fixture onClose={onClose} />);
    const outside = getByRole("button", { name: "Снаружи" });

    fireEvent.mouseDown(outside);
    fireEvent.touchStart(outside);
    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it("ignores the popover, configured trigger and inactive state", () => {
    const onClose = vi.fn();
    const { getByRole, rerender } = render(<Fixture onClose={onClose} />);

    fireEvent.mouseDown(getByRole("button", { name: "Внутри" }));
    fireEvent.mouseDown(getByRole("button", { name: "Триггер" }));
    expect(onClose).not.toHaveBeenCalled();

    rerender(<Fixture active={false} onClose={onClose} />);
    fireEvent.mouseDown(getByRole("button", { name: "Снаружи" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("uses the latest close callback without reinstalling listeners", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { getByRole, rerender } = render(<Fixture onClose={first} />);

    rerender(<Fixture onClose={second} />);
    fireEvent.mouseDown(getByRole("button", { name: "Снаружи" }));

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledOnce();
  });

  it("returns focus to the trigger on Escape only when focus was inside the popover", () => {
    const onClose = vi.fn();
    const { getByRole } = render(<Fixture onClose={onClose} returnFocus />);
    const trigger = getByRole("button", { name: "Триггер" });
    const inside = getByRole("button", { name: "Внутри" });
    const outside = getByRole("button", { name: "Снаружи" });

    inside.focus();
    fireEvent.keyDown(inside, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    expect(trigger).toHaveFocus();

    outside.focus();
    fireEvent.keyDown(outside, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(outside).toHaveFocus();
  });
});
