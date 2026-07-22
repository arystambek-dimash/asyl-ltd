import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { Modal } from "./modal";

function NestedModalHarness() {
  const [outerOpen, setOuterOpen] = useState(false);
  const [innerOpen, setInnerOpen] = useState(false);

  return (
    <>
      <button type="button" onClick={() => setOuterOpen(true)}>
        Открыть внешний
      </button>
      <Modal
        open={outerOpen}
        onClose={() => setOuterOpen(false)}
        title="Внешний диалог"
        description="Описание внешнего"
      >
        <button type="button" onClick={() => setInnerOpen(true)}>
          Открыть внутренний
        </button>
        <Modal open={innerOpen} onClose={() => setInnerOpen(false)} title="Внутренний диалог">
          <input aria-label="Внутреннее поле" />
        </Modal>
      </Modal>
    </>
  );
}

function DisabledNestedTriggerHarness() {
  const [outerOpen, setOuterOpen] = useState(false);
  const [innerOpen, setInnerOpen] = useState(false);
  const [disabled, setDisabled] = useState(false);

  return (
    <>
      <button type="button" onClick={() => setOuterOpen(true)}>
        Открыть
      </button>
      <Modal open={outerOpen} onClose={() => setOuterOpen(false)} title="Внешний">
        <button type="button" disabled={disabled} onClick={() => setInnerOpen(true)}>
          Коррекция
        </button>
        <Modal open={innerOpen} onClose={() => setInnerOpen(false)} title="Коррекция">
          <button
            type="button"
            onClick={() => {
              setDisabled(true);
              setInnerOpen(false);
            }}
          >
            Применить всё
          </button>
        </Modal>
      </Modal>
    </>
  );
}

afterEach(() => {
  document.body.style.overflow = "";
});

describe("Modal", () => {
  it("keeps stacked dialogs isolated and restores focus and scroll lock", async () => {
    document.body.style.overflow = "auto";
    const user = userEvent.setup();
    render(<NestedModalHarness />);

    const outerTrigger = screen.getByRole("button", { name: "Открыть внешний" });
    outerTrigger.focus();
    await user.click(outerTrigger);

    const outerDialog = screen.getByRole("dialog", { name: "Внешний диалог" });
    const outerClose = within(outerDialog).getByRole("button", { name: "Закрыть" });
    const innerTrigger = within(outerDialog).getByRole("button", { name: "Открыть внутренний" });
    expect(outerDialog).toHaveAccessibleDescription("Описание внешнего");
    expect(outerClose).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");

    await user.click(innerTrigger);

    const innerDialog = screen.getByRole("dialog", { name: "Внутренний диалог" });
    const innerClose = within(innerDialog).getByRole("button", { name: "Закрыть" });
    const innerField = within(innerDialog).getByRole("textbox", { name: "Внутреннее поле" });
    expect(innerClose).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");

    innerField.focus();
    await user.tab();
    expect(innerClose).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: "Внутренний диалог" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Внешний диалог" })).toBeInTheDocument();
    expect(document.body.style.overflow).toBe("hidden");
    expect(innerTrigger).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: "Внешний диалог" })).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("auto");
    expect(outerTrigger).toHaveFocus();
  });

  it("falls back inside the outer dialog when a nested opener becomes disabled", async () => {
    const user = userEvent.setup();
    render(<DisabledNestedTriggerHarness />);

    await user.click(screen.getByRole("button", { name: "Открыть" }));
    const outer = screen.getByRole("dialog", { name: "Внешний" });
    await user.click(within(outer).getByRole("button", { name: "Коррекция" }));
    await user.click(
      within(screen.getByRole("dialog", { name: "Коррекция" })).getByRole("button", { name: "Применить всё" }),
    );

    expect(screen.queryByRole("dialog", { name: "Коррекция" })).not.toBeInTheDocument();
    expect(within(outer).getByRole("button", { name: "Закрыть" })).toHaveFocus();
  });
});
