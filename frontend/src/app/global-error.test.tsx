import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import GlobalError from "./global-error";

const sentry = vi.hoisted(() => ({ captureException: vi.fn() }));

vi.mock("@sentry/nextjs", () => sentry);

describe("GlobalError", () => {
  it("reports the boundary error and lets the operator retry", async () => {
    const reset = vi.fn();
    const error = new Error("render failed");
    render(<GlobalError error={error} reset={reset} />);

    await waitFor(() => expect(sentry.captureException).toHaveBeenCalledWith(error));
    expect(screen.getByText(/если ошибка повторится, сообщите администратору/i)).toBeInTheDocument();
    expect(screen.queryByText(/зарегистрирован/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(reset).toHaveBeenCalledOnce();
  });
});
