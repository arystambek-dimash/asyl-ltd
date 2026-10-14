import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AlwaysOnCloseTimeCard } from "./always-on-close-time";

function card(props: Partial<React.ComponentProps<typeof AlwaysOnCloseTimeCard>> = {}) {
  return (
    <AlwaysOnCloseTimeCard
      closeTime="19:00"
      planned={null}
      nextRun="14.10.2026, 19:00"
      timezone="Asia/Almaty"
      needsSetup={false}
      {...props}
    />
  );
}

describe("AlwaysOnCloseTimeCard", () => {
  it("lets the superuser change the hour and closes the editor after saving", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(card({ onSave }));

    await user.click(screen.getByRole("button", { name: "Изменить" }));
    const input = screen.getByLabelText("Время автоприхода");
    expect(input).toHaveValue("19:00");
    await user.clear(input);
    await user.type(input, "21:00");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(onSave).toHaveBeenCalledWith("21:00");
    expect(screen.queryByLabelText("Время автоприхода")).not.toBeInTheDocument();
  });

  it("keeps the editor open with the server error inside the card", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockRejectedValue({ response: { data: { detail: "Нет доступа" } } });
    render(card({ onSave }));

    await user.click(screen.getByRole("button", { name: "Изменить" }));
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Нет доступа");
    expect(screen.getByLabelText("Время автоприхода")).toBeInTheDocument();
  });

  it("shows no edit button without the right and names a planned hour", () => {
    render(card({ planned: { close_time: "21:00", from_day: "2026-10-15" } }));

    expect(screen.queryByRole("button", { name: "Изменить" })).not.toBeInTheDocument();
    expect(screen.getByText("19:00")).toBeInTheDocument();
    expect(screen.getByText("Со смены 15.10.2026 — в 21:00")).toBeInTheDocument();
  });
});
