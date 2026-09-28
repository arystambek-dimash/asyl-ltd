import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WagonReportDraft, WagonReportSent } from "@/lib/wagon-report";
import { WagonReportModal } from "./wagon-report-modal";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: mocks.get, post: mocks.post },
  apiError: (error: Error) => error.message,
}));

const TEXT = "чт 24.09.26 Узбекистан ООО OSIYO NAV NIHOL\nСт. 1 вагон\nД1с-12345678-408 тн";

const draft = (fields: Partial<WagonReportDraft> = {}): WagonReportDraft => ({
  text: TEXT,
  order_ids: [366],
  recipients: [
    { username: "dinara_k", name: "Динара", ready: true },
    { username: "d1maaash", name: "", ready: false },
  ],
  can_send: true,
  reason: "",
  ...fields,
});

const sent = (fields: Partial<WagonReportSent> = {}): WagonReportSent => ({
  sent_at: "2026-09-24T07:45:00+05:00",
  order_ids: [366],
  deliveries: [{ to: "@dinara_k", status: "queued", status_label: "В очереди", error: "" }],
  ...fields,
});

function failure(message: string, code = "") {
  return Object.assign(new Error(message), { response: { status: 400, data: { detail: message, code } } });
}

describe("WagonReportModal", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.post.mockReset();
  });

  it("показывает получателей и отправляет поправленный текст в очередь бота", async () => {
    const user = userEvent.setup();
    const onSent = vi.fn();
    const result = sent();
    mocks.get.mockResolvedValue({ data: draft() });
    mocks.post.mockResolvedValue({ data: result });
    render(<WagonReportModal scope={{ order: 366 }} onClose={vi.fn()} onSent={onSent} />);

    expect(mocks.get).toHaveBeenCalledWith("/loader/wagon-report/compose/?order=366");
    const field = await screen.findByLabelText("Текст отчёта");
    expect(field).toHaveValue(TEXT);
    const recipients = screen.getByRole("region", { name: "Кому" });
    expect(recipients).toHaveTextContent("Динара (@dinara_k)");
    // Кто не писал боту — видно до отправки.
    expect(recipients).toHaveTextContent("@d1maaash — ещё не писал боту /start, не получит");

    await user.type(field, "{end}\nОстальное завтра");
    await user.click(screen.getByRole("button", { name: "Отправить" }));

    expect(mocks.post).toHaveBeenCalledWith("/loader/wagon-report/send/", {
      order_ids: [366],
      text: `${TEXT}\nОстальное завтра`,
      key: expect.stringMatching(/^[A-Za-z0-9_-]{8,64}$/),
    });
    expect(await screen.findByRole("status")).toHaveTextContent(
      "В очереди — бот отправит в течение минуты: @dinara_k.",
    );
    expect(screen.getByRole("button", { name: "Отправлено" })).toBeDisabled();
    expect(screen.getByLabelText("Текст отчёта")).toHaveAttribute("readonly");
    expect(onSent).toHaveBeenCalledWith(result);
  });

  it("отчёт за период — та же очередь, повтор нажатия с тем же ключом", async () => {
    const user = userEvent.setup();
    mocks.get.mockResolvedValue({ data: draft({ order_ids: [366, 367] }) });
    mocks.post.mockRejectedValueOnce(failure("Нет связи с сервером")).mockResolvedValueOnce({ data: sent() });
    render(
      <WagonReportModal
        scope={{ date_from: "2026-09-18", date_to: "2026-09-24", search: "" }}
        onClose={vi.fn()}
        onSent={vi.fn()}
      />,
    );

    expect(mocks.get).toHaveBeenCalledWith("/loader/wagon-report/compose/?date_from=2026-09-18&date_to=2026-09-24");
    await user.click(await screen.findByRole("button", { name: "Отправить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с сервером");
    await user.click(screen.getByRole("button", { name: "Отправить" }));

    await screen.findByRole("status");
    expect(mocks.post.mock.calls[1][1]).toEqual(mocks.post.mock.calls[0][1]);
    expect(mocks.post.mock.calls[0][1]).toMatchObject({ order_ids: [366, 367] });
  });

  it.each([
    ["bot_off", /Telegram-бот сейчас не работает/],
    ["no_recipients", /Не выбрано, кому отправлять отчёт/],
    ["not_started", /не написал боту \/start/],
  ] as const)("отправить нельзя (%s) — окно говорит почему, текст можно скопировать", async (reason, text) => {
    mocks.get.mockResolvedValue({ data: draft({ can_send: false, reason }) });
    render(<WagonReportModal scope={{ order: 366 }} onClose={vi.fn()} onSent={vi.fn()} />);

    expect(await screen.findByRole("note")).toHaveTextContent(text);
    expect(screen.getByRole("button", { name: "Отправить" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Скопировать/ })).toBeEnabled();
  });

  it("бота выключили, пока окно было открыто — ошибка в окне, причина обновляется, текст остаётся", async () => {
    const user = userEvent.setup();
    const onSent = vi.fn();
    mocks.get
      .mockResolvedValueOnce({ data: draft() })
      .mockResolvedValueOnce({ data: draft({ text: "другой текст", can_send: false, reason: "bot_off" }) });
    mocks.post.mockRejectedValueOnce(
      failure("Telegram-бот сейчас не работает — отчёт не отправить. Скопируйте текст", "report_bot_off"),
    );
    render(<WagonReportModal scope={{ order: 366 }} onClose={vi.fn()} onSent={onSent} />);

    await user.click(await screen.findByRole("button", { name: "Отправить" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Telegram-бот сейчас не работает");
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText("Текст отчёта")).toHaveValue(TEXT);
    expect(await screen.findByRole("note")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отправить" })).toBeDisabled();
    expect(onSent).not.toHaveBeenCalled();
  });

  it("не составился отчёт — ошибка в окне, отправить нечего", async () => {
    mocks.get.mockRejectedValue(failure("Дата в формате ГГГГ-ММ-ДД"));
    render(<WagonReportModal scope={{ order: 366 }} onClose={vi.fn()} onSent={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Дата в формате ГГГГ-ММ-ДД");
    expect(screen.getByRole("button", { name: "Отправить" })).toBeDisabled();
  });

  it("за пустой период отправлять нечего", async () => {
    mocks.get.mockResolvedValue({ data: draft({ text: "", order_ids: [] }) });
    render(
      <WagonReportModal
        scope={{ date_from: "2026-09-24", date_to: "2026-09-24", search: "" }}
        onClose={vi.fn()}
        onSent={vi.fn()}
      />,
    );

    expect(await screen.findByText(/нет отгрузок вагонов/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отправить" })).toBeDisabled();
  });
});
