import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EventsPage from "./page";

const mocks = vi.hoisted(() => ({
  useApi: vi.fn(),
  reload: vi.fn(),
}));

vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/lib/use-debounced", () => ({ useDebounced: (value: string) => value }));
vi.mock("@/lib/use-local-day", () => ({ useLocalDay: () => "2026-07-30" }));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));

const event = {
  id: 1,
  event_type: "status",
  message: "Статус изменён",
  user: 1,
  user_name: "operator",
  order: 12,
  payload: {},
  created_at: "2026-07-30T10:00:00Z",
};

describe("EventsPage pagination", () => {
  beforeEach(() => {
    mocks.reload.mockReset();
    mocks.useApi.mockReset().mockReturnValue({
      data: {
        count: 205,
        next: "http://testserver/api/events/?page=2&page_size=100",
        previous: null,
        results: [event],
      },
      loading: false,
      error: "",
      reload: mocks.reload,
    });
  });

  it("shows the total and requests the next server-side page", async () => {
    const user = userEvent.setup();
    render(<EventsPage />);

    expect(mocks.useApi).toHaveBeenLastCalledWith("/events/?page=1&page_size=100");
    expect(screen.getByText("События 1–100 из 205 · страница 1 из 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Назад" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Далее" }));

    expect(mocks.useApi).toHaveBeenLastCalledWith("/events/?page=2&page_size=100");
  });

  it("returns to the first page when a filter changes", async () => {
    const user = userEvent.setup();
    render(<EventsPage />);

    await user.click(screen.getByRole("button", { name: "Далее" }));
    await user.type(screen.getByLabelText("Поиск"), "оплата");

    expect(mocks.useApi).toHaveBeenLastCalledWith(
      "/events/?page=1&page_size=100&search=%D0%BE%D0%BF%D0%BB%D0%B0%D1%82%D0%B0",
    );
  });
});
