import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EventsPage from "./page";

const getMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { get: getMock },
  apiError: () => "request failed",
  isCanceledRequest: () => false,
}));
vi.mock("@/lib/use-debounced", () => ({ useDebounced: (value: string) => value }));
vi.mock("@/lib/use-local-day", () => ({ useLocalDay: () => "2026-07-30" }));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));

const event = (id: number) => ({
  id,
  event_type: "status",
  message: `Событие ${id}`,
  user: 1,
  user_name: "operator",
  order: 12,
  payload: {},
  created_at: "2026-07-30T10:00:00Z",
});

function page(ids: number[], count: number, next: string | null) {
  return { data: { count, next, previous: null, results: ids.map(event) } };
}

describe("EventsPage lazy pagination", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("догружает следующую страницу в конец ленты", async () => {
    const user = userEvent.setup();
    getMock.mockResolvedValueOnce(page([1], 205, "next-url"));
    render(<EventsPage />);

    await waitFor(() => expect(screen.getByText(/Событие 1/)).toBeInTheDocument());
    expect(getMock).toHaveBeenLastCalledWith("/events/?page=1&page_size=100", expect.anything());
    expect(screen.getByText("1 из 205")).toBeInTheDocument();

    getMock.mockResolvedValueOnce(page([2], 205, null));
    await user.click(screen.getByRole("button", { name: "Показать ещё" }));

    await waitFor(() => expect(screen.getByText(/Событие 2/)).toBeInTheDocument());
    expect(getMock).toHaveBeenLastCalledWith("/events/?page=2&page_size=100", expect.anything());
    // Первая страница осталась на месте, кнопка исчезла — всё загружено.
    expect(screen.getByText(/Событие 1/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Показать ещё" })).not.toBeInTheDocument();
  });

  it("смена фильтра начинает ленту заново с первой страницы", async () => {
    const user = userEvent.setup();
    getMock.mockResolvedValue(page([1], 1, null));
    render(<EventsPage />);
    await waitFor(() => expect(screen.getByText(/Событие 1/)).toBeInTheDocument());

    await user.type(screen.getByLabelText("Поиск"), "оплата");

    await waitFor(() =>
      expect(getMock).toHaveBeenLastCalledWith(
        "/events/?search=%D0%BE%D0%BF%D0%BB%D0%B0%D1%82%D0%B0&page=1&page_size=100",
        expect.anything(),
      ),
    );
  });
});
