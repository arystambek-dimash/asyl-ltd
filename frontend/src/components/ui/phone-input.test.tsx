import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { PhoneInput, type PhoneInputProps } from "./phone-input";

function Field({
  initial = "",
  onValue,
  ...props
}: Partial<PhoneInputProps> & { initial?: string; onValue?: (value: string) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <PhoneInput
      aria-label="Телефон"
      value={value}
      onChange={(next) => {
        setValue(next);
        onValue?.(next);
      }}
      {...props}
    />
  );
}

describe("PhoneInput", () => {
  it("набирает номер по маске страны", async () => {
    const user = userEvent.setup();
    const onValue = vi.fn();
    render(<Field onValue={onValue} />);

    await user.type(screen.getByLabelText("Телефон"), "7055656565");

    expect(screen.getByLabelText("Телефон")).toHaveValue("(705) 565-65-65");
    expect(onValue).toHaveBeenLastCalledWith("+7 (705) 565-65-65");
    expect(screen.getByLabelText("Страна телефона")).toHaveValue("Казахстан");
  });

  it("правка в середине номера оставляет курсор на месте", async () => {
    const user = userEvent.setup();
    render(<Field initial="+7 (705) 565-65-65" />);
    const input = screen.getByLabelText<HTMLInputElement>("Телефон");

    // Курсор после «(705» — стираем пятёрку.
    input.focus();
    input.setSelectionRange(4, 4);
    await user.keyboard("{Backspace}");

    expect(input).toHaveValue("(705) 656-56-5");
    expect(input.selectionStart).toBe(3);
  });

  it("помечает оболочку поля как ошибочную по aria-invalid", () => {
    render(<Field aria-invalid="true" />);

    const wrapper = screen.getByLabelText("Телефон").closest("div")?.parentElement;
    expect(wrapper?.className).toContain("border-[var(--destructive)]");
  });
});
