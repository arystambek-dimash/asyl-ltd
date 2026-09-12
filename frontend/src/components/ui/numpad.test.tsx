import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Numpad } from "./numpad";

it("reports digits and backspace", async () => {
  const user = userEvent.setup();
  const onDigit = vi.fn();
  const onBackspace = vi.fn();
  render(<Numpad onDigit={onDigit} onBackspace={onBackspace} />);

  await user.click(screen.getByRole("button", { name: "Цифра 7" }));
  await user.click(screen.getByRole("button", { name: "Цифра 0" }));
  await user.click(screen.getByRole("button", { name: "Стереть" }));

  expect(onDigit.mock.calls).toEqual([["7"], ["0"]]);
  expect(onBackspace).toHaveBeenCalledTimes(1);
});

it("disables every key while busy", () => {
  render(<Numpad onDigit={vi.fn()} onBackspace={vi.fn()} disabled />);
  const keys = screen.getAllByRole("button");
  expect(keys).toHaveLength(11);
  keys.forEach((key) => expect(key).toBeDisabled());
});
