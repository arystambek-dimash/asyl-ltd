import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { Me } from "@/lib/types";
import { OnboardingTour, TOUR_START_EVENT } from "./onboarding-tour";

const me: Me = {
  id: 1,
  username: "operator",
  is_client: false,
  is_superuser: false,
  is_monoblock: false,
  monoblock_name: null,
  monoblock_camera: null,
  permissions: [],
  role_name: "Оператор",
  client_id: null,
  sales_department: null,
};

function Harness() {
  return (
    <>
      <button type="button" onClick={() => window.dispatchEvent(new Event(TOUR_START_EVENT))}>
        Начать обучение
      </button>
      <button type="button">Фоновое действие</button>
      <OnboardingTour me={me} />
    </>
  );
}

afterEach(() => {
  localStorage.clear();
});

describe("OnboardingTour", () => {
  it("isolates the modal, traps focus, closes on Escape, and restores the opener", async () => {
    localStorage.setItem("asyl_tour_v1", "1");
    const user = userEvent.setup();
    render(<Harness />);

    const opener = screen.getByRole("button", { name: "Начать обучение" });
    const backgroundAction = screen.getByRole("button", { name: "Фоновое действие" });
    await user.click(opener);

    const dialog = screen.getByRole("dialog", { name: "Меню навигации" });
    await waitFor(() => expect(dialog).toHaveFocus());
    expect(dialog).toHaveAccessibleDescription(/Слева — разделы системы/);
    expect(opener).toHaveAttribute("inert");
    expect(opener).toHaveAttribute("aria-hidden", "true");
    expect(backgroundAction).toHaveAttribute("inert");

    const close = screen.getByRole("button", { name: "Закрыть обучение" });
    const next = screen.getByRole("button", { name: "Далее" });
    await user.tab();
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(next).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(opener).not.toHaveAttribute("inert");
    expect(opener).not.toHaveAttribute("aria-hidden");
    expect(backgroundAction).not.toHaveAttribute("inert");
  });
});
