import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ShippingSegment,
  ShippingSession,
  ShippingSessionSettings,
  ShippingSessionsPage,
} from "@/lib/shipping-sessions";
import { ShippingSessionsPanel } from "./shipping-sessions-panel";

const mocks = vi.hoisted(() => ({
  page: null as ShippingSessionsPage | null,
  settings: { idle_timeout_seconds: 300, can_manage: true } as ShippingSessionSettings,
  error: "",
  errorStatus: null as number | null,
  urls: [] as (string | null)[],
  reload: vi.fn().mockResolvedValue(undefined),
  setSettings: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  poll: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: { post: mocks.post, patch: mocks.patch }, apiError: (e: Error) => e.message }));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => {
    mocks.urls.push(url);
    const isSettings = url === "/cameras/shipping-session-settings/";
    return {
      data: isSettings ? mocks.settings : mocks.page,
      loading: false,
      error: isSettings ? "" : mocks.error,
      errorStatus: isSettings ? null : mocks.errorStatus,
      reload: mocks.reload,
      setData: mocks.setSettings,
    };
  },
}));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: mocks.poll }));

function segment(overrides: Partial<ShippingSegment> = {}): ShippingSegment {
  return {
    id: 101,
    number: "111AAA01",
    number_source: "model",
    identity_status: "identified",
    identity_error: "",
    started_at: "2026-01-01T05:00:00Z",
    last_counted_at: "2026-01-01T05:10:00Z",
    ended_at: "2026-01-01T05:15:00Z",
    total_bags: 120,
    photo_url: "/fixture/segment-101.jpg",
    photo_taken_at: "2026-01-01T05:00:02Z",
    idle_timeout_seconds: 300,
    can_identify: false,
    ...overrides,
  };
}
function session(overrides: Partial<ShippingSession> = {}): ShippingSession {
  return {
    id: 10,
    camera: "cam2",
    recognition_model: "vehicle_number",
    number: "111AAA01",
    status: "active",
    total_bags: 160,
    started_at: "2026-01-01T05:00:00Z",
    last_counted_at: "2026-01-01T05:21:00Z",
    ended_at: null,
    order_id: null,
    segments: [segment(), segment({ id: 102, total_bags: 40, ended_at: null, number_source: "gpt" })],
    ...overrides,
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.urls = [];
  mocks.error = "";
  mocks.errorStatus = null;
  mocks.settings = { idle_timeout_seconds: 300, can_manage: true };
  mocks.page = { results: [session()], next_cursor: null };
});

describe("ShippingSessionsPanel", () => {
  it("shows a combined vehicle total but keeps each pause-separated segment and document distinct without an order", () => {
    render(<ShippingSessionsPanel />);
    expect(screen.getByText("Машина 111AAA01")).toBeInTheDocument();
    expect(screen.getByText("160 меш.")).toBeInTheDocument();
    expect(screen.getByText("Без заказа · мешки учитываются в сессии")).toBeInTheDocument();
    const first = screen.getByRole("article", { name: "Отрезок 101" });
    const second = screen.getByRole("article", { name: "Отрезок 102" });
    expect(within(first).getByText("120 меш.")).toBeInTheDocument();
    expect(within(first).getByText("Закрыт")).toBeInTheDocument();
    expect(within(second).getByText("40 меш.")).toBeInTheDocument();
    expect(within(second).getByText("Идёт подсчёт")).toBeInTheDocument();
    expect(within(first).getByText("Номер: Модель")).toBeInTheDocument();
    expect(within(second).getByText("Номер: GPT")).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: "Накладная отрезка" })).toHaveAttribute(
      "href",
      "/monoblock/shipping-segments/101/print",
    );
    expect(within(second).getByRole("link", { name: "Накладная отрезка" })).toHaveAttribute(
      "href",
      "/monoblock/shipping-segments/102/print",
    );
    expect(screen.queryByRole("button", { name: /Начать|Привязать|Завершить/ })).toBeNull();
    expect(mocks.poll).toHaveBeenCalledWith(mocks.reload, 3000);
  });

  it("keeps unknown bags and photo failures visible and submits a manual number only for that segment", async () => {
    const user = userEvent.setup();
    const unknown = segment({
      number: "",
      number_source: "",
      identity_status: "unidentified",
      identity_error: "photo_unavailable",
      can_identify: true,
    });
    mocks.page = { results: [session({ number: "", total_bags: 120, segments: [unknown] })], next_cursor: null };
    mocks.post.mockResolvedValue({ data: { ...unknown, number: "222BBB02", number_source: "manual" } });
    render(<ShippingSessionsPanel />);
    fireEvent.error(screen.getByRole("img", { name: "Кадр номера отрезка 101" }));
    expect(screen.getByText("Кадр недоступен")).toBeInTheDocument();
    expect(screen.getByText(/Кадр номера недоступен. Подсчёт мешков сохранён/)).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Указать номер отрезка #101" }), " 222bbb02 ");
    await user.click(screen.getByRole("button", { name: "Сохранить номер" }));
    expect(mocks.post).toHaveBeenCalledWith("/cameras/shipping-segments/101/identify/", { number: "222BBB02" });
    expect(mocks.reload).toHaveBeenCalledOnce();
    expect(screen.getByRole("status")).toHaveTextContent("Номер 222BBB02 сохранён");
  });

  it("preserves the unknown record and typed number when the server refuses identification", async () => {
    const user = userEvent.setup();
    mocks.page = {
      results: [
        session({
          number: "",
          segments: [segment({ number: "", can_identify: true, identity_status: "unidentified" })],
        }),
      ],
      next_cursor: null,
    };
    mocks.post.mockRejectedValue(new Error("Номер уже изменён другим сотрудником"));
    render(<ShippingSessionsPanel />);
    await user.type(screen.getByRole("textbox"), "333CCC03");
    await user.click(screen.getByRole("button", { name: "Сохранить номер" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Номер уже изменён");
    expect(screen.getByRole("textbox")).toHaveValue("333CCC03");
    expect(mocks.reload).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("makes view-only settings and unidentified segments read-only", () => {
    mocks.settings.can_manage = false;
    mocks.page = {
      results: [session({ number: "", segments: [segment({ number: "", can_identify: false })] })],
      next_cursor: null,
    };
    render(<ShippingSessionsPanel />);
    expect(screen.getByText("Закрытие по простою: 5 мин.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Простой:/ })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("saves an operator's idle timeout in seconds and explains existing segment snapshots", async () => {
    const user = userEvent.setup();
    mocks.patch.mockResolvedValue({ data: { idle_timeout_seconds: 450, can_manage: true } });
    render(<ShippingSessionsPanel />);
    await user.click(screen.getByRole("button", { name: "Простой: 5 мин." }));
    const modal = screen.getByRole("dialog");
    expect(within(modal).getByText(/открытые сохраняют прежнюю настройку/)).toBeInTheDocument();
    const input = within(modal).getByRole("spinbutton", { name: "Простой, минут" });
    await user.clear(input);
    await user.type(input, "7.5");
    await user.click(within(modal).getByRole("button", { name: "Сохранить настройку" }));
    expect(mocks.patch).toHaveBeenCalledWith("/cameras/shipping-session-settings/", { idle_timeout_seconds: 450 });
    expect(mocks.setSettings).toHaveBeenCalledWith({ idle_timeout_seconds: 450, can_manage: true });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does not claim a setting was saved on permission failure", async () => {
    const user = userEvent.setup();
    mocks.patch.mockRejectedValue(new Error("Недостаточно прав"));
    render(<ShippingSessionsPanel />);
    await user.click(screen.getByRole("button", { name: "Простой: 5 мин." }));
    await user.click(screen.getByRole("button", { name: "Сохранить настройку" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Недостаточно прав");
    expect(mocks.setSettings).not.toHaveBeenCalled();
  });

  it("pages through saved sessions and resets the cursor when switching conveyor", async () => {
    const user = userEvent.setup();
    mocks.page!.next_cursor = 9;
    render(
      <ShippingSessionsPanel
        cameras={[
          { src: "cam2", name: "Конвейер А" },
          { src: "cam3", name: "Конвейер Б" },
        ]}
      />,
    );
    const toolbar = screen.getByRole("group", { name: "Фильтры и настройки сессий" });
    expect(within(toolbar).getByRole("combobox", { name: "Конвейер" })).toBeInTheDocument();
    expect(within(toolbar).getByRole("button", { name: "Простой: 5 мин." })).toBeInTheDocument();
    expect(within(toolbar).getByRole("button", { name: "Обновить сессии" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    expect(mocks.urls).toContain("/cameras/shipping-sessions/?cursor=9");
    await user.selectOptions(screen.getByRole("combobox", { name: "Конвейер" }), "cam3");
    expect(mocks.urls).toContain("/cameras/shipping-sessions/?camera=cam3");
    expect(mocks.urls).not.toContain("/cameras/shipping-sessions/?camera=cam3&cursor=9");
  });

  it("shows an API outage beside retained counts instead of presenting an empty successful list", () => {
    mocks.error = "Сервер недоступен";
    render(<ShippingSessionsPanel />);
    expect(screen.getByRole("alert")).toHaveTextContent("Сервер недоступен");
    expect(screen.getByText("160 меш.")).toBeInTheDocument();
    expect(screen.queryByText("Отгрузок пока нет")).toBeNull();
  });

  it("does not download the same saved photo again when polling renews its signed URL", () => {
    mocks.page = {
      results: [session({ segments: [segment({ photo_url: "/fixture/image.jpg?token=first" })] })],
      next_cursor: null,
    };
    const { rerender } = render(<ShippingSessionsPanel />);
    const initialUrl = screen.getByRole("img").getAttribute("src");
    mocks.page.results[0].segments[0] = segment({ photo_url: "/fixture/image.jpg?token=second" });
    rerender(<ShippingSessionsPanel />);
    expect(screen.getByRole("img")).toHaveAttribute("src", initialUrl);
    expect(screen.getByRole("link", { name: "Открыть кадр отрезка 101" })).toHaveAttribute(
      "href",
      "/fixture/image.jpg?token=second",
    );
  });
});
