import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import DashboardError from "./error";

const sentry = vi.hoisted(() => ({ captureException: vi.fn() }));

vi.mock("@sentry/nextjs", () => sentry);

describe("DashboardError", () => {
  it("reports the caught error and preserves reset", async () => {
    const reset = vi.fn();
    const error = new Error("dashboard failed");
    render(<DashboardError error={error} reset={reset} />);

    await waitFor(() => expect(sentry.captureException).toHaveBeenCalledWith(error));
    await userEvent.click(screen.getByRole("button", { name: "Обновить" }));
    expect(reset).toHaveBeenCalledOnce();
  });
});
