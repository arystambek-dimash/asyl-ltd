import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ErrorAlert, FormError } from "./data-state";

describe("FormError", () => {
  it("renders nothing without a message", () => {
    const { container } = render(<FormError message="" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("announces the message and keeps extra layout classes", () => {
    render(<FormError message="Не удалось сохранить" className="sm:col-span-2" />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Не удалось сохранить");
    expect(alert).toHaveClass("sm:col-span-2");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("ErrorAlert", () => {
  it("shows «Повторить» only with onRetry", async () => {
    const onRetry = vi.fn();
    const { rerender } = render(<ErrorAlert message="Нет связи" />);
    expect(screen.queryByRole("button", { name: /Повторить/ })).not.toBeInTheDocument();

    rerender(<ErrorAlert message="Нет связи" onRetry={onRetry} />);
    await userEvent.click(screen.getByRole("button", { name: /Повторить/ }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
