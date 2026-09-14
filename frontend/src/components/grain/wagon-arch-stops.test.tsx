import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { grainTripHref } from "@/lib/grain";
import type { WagonArchStop } from "@/lib/types";
import { WagonArchStops } from "./wagon-arch-stops";

type StopsPage = { results: WagonArchStop[]; next_cursor: number | null };

const mocks = vi.hoisted(() => ({
  urls: [] as string[],
  page: { results: [] as WagonArchStop[], next_cursor: null as number | null } as StopsPage | null,
  loading: false,
  reload: vi.fn(),
  setData: vi.fn(),
  polling: vi.fn(),
  post: vi.fn(),
  can: vi.fn(() => true),
}));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string) => {
    mocks.urls.push(url);
    return { data: mocks.page, loading: mocks.loading, error: "", reload: mocks.reload, setData: mocks.setData };
  },
}));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: mocks.polling }));
vi.mock("@/lib/api", () => ({
  api: { defaults: { baseURL: "https://crm.test/api" }, post: mocks.post },
  apiError: () => "Не удалось закрыть стоянку",
}));
vi.mock("@/lib/can", () => ({ can: mocks.can }));
vi.mock("@/store/auth", () => ({
  useAuth: (selector: (state: { me: { is_superuser: boolean; permissions: string[] } }) => unknown) =>
    selector({ me: { is_superuser: true, permissions: ["grain.edit"] } }),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

function stop(overrides: Partial<WagonArchStop> = {}): WagonArchStop {
  return {
    id: 7,
    stop_id: "stop-1",
    camera: "cam8",
    arrived_at: "2026-09-14T04:00:00Z",
    full_weight_kg: 62340,
    exit_weight_kg: 24120,
    net_kg: 38220,
    number: "28055531",
    number_source: "model",
    recognition_error: "",
    ocr_attempts: 1,
    status: "closed",
    blocked_reason: "",
    blocked_detail: "",
    motion_gap: false,
    departed_at: "2026-09-14T04:40:00Z",
    entry_applied_at: "2026-09-14T04:00:10Z",
    exit_applied_at: "2026-09-14T04:50:00Z",
    wagon_id: 124,
    wagon_status: "completed",
    continues: null,
    photo_url: "/api/grain/photos/evidence/9/?token=abc",
    ...overrides,
  };
}

beforeEach(() => {
  mocks.urls = [];
  mocks.loading = false;
  mocks.reload.mockReset();
  mocks.setData.mockReset();
  mocks.polling.mockReset();
  mocks.post.mockReset();
  mocks.can.mockReset();
  mocks.can.mockReturnValue(true);
  mocks.page = {
    results: [
      stop(),
      stop({
        id: 6,
        stop_id: "stop-0",
        number: "",
        status: "open",
        blocked_reason: "silo_required",
        exit_weight_kg: null,
        net_kg: null,
        wagon_id: 123,
        wagon_status: "arrived",
        photo_url: null,
      }),
      stop({
        id: 5,
        stop_id: "stop-x",
        status: "attention",
        blocked_reason: "exit_not_lower",
        exit_weight_kg: 70000,
        net_kg: -7660,
      }),
    ],
    next_cursor: 5,
  };
});

describe("WagonArchStops", () => {
  it("lists stops newest first with weights, status, reason and the trip link", () => {
    render(<WagonArchStops />);
    expect(mocks.urls).toContain("/grain/wagon-arch/stops/");
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, true);
    const [first, second, third] = screen.getAllByRole("listitem");
    expect(first).toHaveTextContent("Вагон 28055531");
    expect(first).toHaveTextContent("62 340 кг → 24 120 кг");
    expect(first).toHaveTextContent("нетто 38 220 кг");
    expect(within(first).getByText("Завершена")).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: "Открыть рейс вагона 28055531" })).toHaveAttribute(
      "href",
      grainTripHref({ id: 124, direction: "intake" }),
    );
    expect(within(first).getByRole("img", { name: "Стоянка 7" })).toHaveAttribute(
      "src",
      "https://crm.test/api/grain/photos/evidence/9/?token=abc",
    );
    expect(second).toHaveTextContent("Номер не распознан");
    expect(within(second).getByText("Ждёт")).toBeInTheDocument();
    expect(second).toHaveTextContent("Назначьте силос в рейсе — заезд запишется автоматически");
    expect(within(third).getByText("Нужна проверка")).toBeInTheDocument();
    expect(third).toHaveTextContent("Вес на выезде не меньше веса на въезде");
  });

  it("pages to older stops and stops live polling there", async () => {
    const user = userEvent.setup();
    render(<WagonArchStops />);
    await user.click(screen.getByRole("button", { name: "Более ранние" }));
    expect(mocks.urls).toContain("/grain/wagon-arch/stops/?before=5");
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, false);
  });

  it("explains an empty journal without rendering the bordered list (m4)", () => {
    mocks.page = { results: [], next_cursor: null };
    render(<WagonArchStops />);
    expect(screen.getByText("Стоянок под аркой пока нет")).toBeInTheDocument();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("shows a loading state on the first page before any data has arrived (m4)", () => {
    mocks.page = null;
    mocks.loading = true;
    render(<WagonArchStops />);
    expect(screen.getByText("Загрузка…")).toBeInTheDocument();
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.queryByText("Стоянок под аркой пока нет")).toBeNull();
  });

  it("marks a re-positioned stop as «продолжение стоянки» instead of showing нетто (m6)", () => {
    mocks.page = {
      results: [stop({ id: 8, continues: 7, net_kg: 12000, exit_weight_kg: 50340 })],
      next_cursor: null,
    };
    render(<WagonArchStops />);
    const [row] = screen.getAllByRole("listitem");
    expect(row).toHaveTextContent("продолжение стоянки");
    expect(row).not.toHaveTextContent("нетто");
    // Weights themselves stay visible.
    expect(row).toHaveTextContent("62 340 кг → 50 340 кг");
  });

  it("labels the trip link «без номера» when the wagon number is blank (m8)", () => {
    mocks.page = {
      results: [stop({ id: 13, number: "", wagon_id: 55 })],
      next_cursor: null,
    };
    render(<WagonArchStops />);
    const [row] = screen.getAllByRole("listitem");
    expect(within(row).getByRole("link", { name: "Открыть рейс вагона без номера" })).toBeInTheDocument();
  });

  it("shows a muted badge with the raw value for an unknown status (m5)", () => {
    mocks.page = {
      // Casting to bypass the TS union — this is exactly the defensive case m5 covers.
      results: [stop({ id: 12, status: "future_status" as WagonArchStop["status"] })],
      next_cursor: null,
    };
    render(<WagonArchStops />);
    const [row] = screen.getAllByRole("listitem");
    const badge = within(row).getByText("future_status");
    expect(badge).toBeInTheDocument();
    expect(badge.className).toContain("bg-[var(--muted)]");
    expect(badge.className).not.toContain("bg-[var(--success)]");
  });

  it("labels a superseded stop «Переставлен» (m6)", () => {
    mocks.page = {
      results: [stop({ id: 4, stop_id: "stop-superseded", status: "superseded" })],
      next_cursor: null,
    };
    render(<WagonArchStops />);
    const [row] = screen.getAllByRole("listitem");
    expect(within(row).getByText("Переставлен")).toBeInTheDocument();
  });

  it("shows a muted operator note for closed stops dismissed or recorded manually, without warning styling", () => {
    mocks.page = {
      results: [
        stop({ id: 9, status: "closed", blocked_reason: "", blocked_detail: "закрыто оператором" }),
        stop({ id: 10, status: "closed", blocked_reason: "", blocked_detail: "записано вручную" }),
      ],
      next_cursor: null,
    };
    render(<WagonArchStops />);
    const [first, second] = screen.getAllByRole("listitem");
    expect(within(first).getByText("Завершена")).toBeInTheDocument();
    expect(first).toHaveTextContent("закрыто оператором");
    expect(within(second).getByText("Завершена")).toBeInTheDocument();
    expect(second).toHaveTextContent("записано вручную");
  });

  it("derives the operator note from shape, not a hard-coded string list (m3)", () => {
    mocks.page = {
      results: [
        // Any closed stop with an empty reason and a non-empty detail is an operator note —
        // not just the two literal strings the old OPERATOR_NOTE_DETAILS set knew about.
        stop({ id: 14, status: "closed", blocked_reason: "", blocked_detail: "новая заметка оператора" }),
      ],
      next_cursor: null,
    };
    render(<WagonArchStops />);
    const [row] = screen.getAllByRole("listitem");
    expect(row).toHaveTextContent("новая заметка оператора");
    // Muted note, not the warning-styled reason text.
    expect(row.querySelector(".text-\\[var\\(--warning\\)\\]")).toBeNull();
  });

  it("does not show a muted note for a closed stop that still carries a blocked_reason (m3)", () => {
    mocks.page = {
      results: [
        stop({
          id: 15,
          status: "closed",
          blocked_reason: "exit_not_lower",
          blocked_detail: "Вес на выезде не меньше веса на въезде",
        }),
      ],
      next_cursor: null,
    };
    render(<WagonArchStops />);
    const [row] = screen.getAllByRole("listitem");
    // A non-empty blocked_reason is a real warning, not a plain operator note.
    expect(row.querySelector(".text-\\[var\\(--warning\\)\\]")).not.toBeNull();
  });

  it("never presents weight_discrepancy as an import problem", () => {
    mocks.page = {
      results: [
        stop({ id: 11, status: "closed", wagon_status: "weight_discrepancy", blocked_reason: "", blocked_detail: "" }),
      ],
      next_cursor: null,
    };
    render(<WagonArchStops />);
    const [row] = screen.getAllByRole("listitem");
    expect(within(row).getByText("Завершена")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Открыть рейс вагона 28055531" })).toBeInTheDocument();
  });

  it("does not overwrite a poll that lands while a dismiss is in flight (m2)", async () => {
    // The dismiss POST is held open; a poll "lands" (mocks.page changes + rerender)
    // before it resolves. replaceRow must build its replacement from the LATEST
    // data, not the stale snapshot captured when the dismiss button was clicked.
    let resolveDismiss!: (value: { data: WagonArchStop }) => void;
    mocks.post.mockReturnValue(
      new Promise<{ data: WagonArchStop }>((resolve) => {
        resolveDismiss = resolve;
      }),
    );
    const user = userEvent.setup();
    const { rerender } = render(<WagonArchStops />);
    const attentionRow = screen.getAllByRole("listitem")[2];
    await user.click(within(attentionRow).getByRole("button", { name: "Закрыть стоянку" }));

    // A poll lands while the dismiss is still pending: a brand-new row (id 99) appears.
    const polledRow = stop({ id: 99, stop_id: "stop-polled", status: "open", blocked_reason: "silo_required" });
    mocks.page = { results: [...(mocks.page?.results ?? []), polledRow], next_cursor: 5 };
    rerender(<WagonArchStops />);

    resolveDismiss({
      data: stop({
        id: 5,
        stop_id: "stop-x",
        status: "closed",
        blocked_reason: "",
        blocked_detail: "закрыто оператором",
      }),
    });
    // setData is a mock (no real state), so the dismiss's effect is only visible
    // through its call args, not a DOM update — wait for that call to land.
    await vi.waitFor(() => expect(mocks.setData).toHaveBeenCalled());

    // The row the poll delivered must still be there — replaceRow must not have
    // reverted to the pre-poll snapshot captured when the dismiss button was clicked.
    const updated = mocks.setData.mock.calls.at(-1)?.[0] as { results: WagonArchStop[] };
    expect(updated.results.some((r) => r.id === 99)).toBe(true);
    expect(updated.results.find((r) => r.id === 5)?.blocked_detail).toBe("закрыто оператором");
  });

  it("lets a grain.edit user dismiss an attention stop; the row updates in place from the response", async () => {
    mocks.post.mockResolvedValue({
      data: stop({
        id: 5,
        stop_id: "stop-x",
        status: "closed",
        blocked_reason: "",
        blocked_detail: "закрыто оператором",
      }),
    });
    const user = userEvent.setup();
    render(<WagonArchStops />);
    const rows = screen.getAllByRole("listitem");
    const attentionRow = rows[2];
    const dismissButton = within(attentionRow).getByRole("button", { name: "Закрыть стоянку" });
    await user.click(dismissButton);
    expect(mocks.post).toHaveBeenCalledWith("/grain/wagon-arch/stops/5/dismiss/");
    expect(mocks.setData).toHaveBeenCalledTimes(1);
    const updated = mocks.setData.mock.calls[0][0] as { results: WagonArchStop[] };
    expect(updated.results.find((r) => r.id === 5)?.status).toBe("closed");
    expect(mocks.reload).not.toHaveBeenCalled();
  });

  it("shows the dismiss button on an open stop with a blocked_reason too", () => {
    render(<WagonArchStops />);
    const rows = screen.getAllByRole("listitem");
    const openRow = rows[1];
    expect(within(openRow).getByRole("button", { name: "Закрыть стоянку" })).toBeInTheDocument();
  });

  it("hides the dismiss button without grain.edit", () => {
    mocks.can.mockReturnValue(false);
    render(<WagonArchStops />);
    expect(screen.queryByRole("button", { name: "Закрыть стоянку" })).not.toBeInTheDocument();
  });

  it("renders a dismiss error inline in the row", async () => {
    mocks.post.mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    render(<WagonArchStops />);
    const rows = screen.getAllByRole("listitem");
    const attentionRow = rows[2];
    await user.click(within(attentionRow).getByRole("button", { name: "Закрыть стоянку" }));
    expect(await within(attentionRow).findByRole("alert")).toHaveTextContent("Не удалось закрыть стоянку");
  });
});
