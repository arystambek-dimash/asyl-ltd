import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
  recipient: { name: "Динара", to: "Динаре", phone: "", chat_name: "" },
  delivery: "link",
  ...fields,
});

const sent = (fields: Partial<WagonReportSent> = {}): WagonReportSent => ({
  status: "link",
  status_label: "Ссылкой WhatsApp",
  sent_at: "2026-09-24T07:45:00+05:00",
  order_ids: [366],
  recipient: { name: "Динара", to: "Динаре", phone: "" },
  error: "",
  ...fields,
});

function failure(message: string, code = "") {
  return Object.assign(new Error(message), { response: { status: 400, data: { detail: message, code } } });
}

describe("WagonReportModal", () => {
  const open = vi.fn();

  beforeEach(() => {
    mocks.get.mockReset();
    mocks.post.mockReset();
    open.mockReset();
    vi.stubGlobal("open", open);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("составляет отчёт одной отгрузки и без номера получателя открывает выбор чата в WhatsApp", async () => {
    const user = userEvent.setup();
    const onSent = vi.fn();
    const result = sent();
    mocks.get.mockResolvedValue({ data: draft() });
    mocks.post.mockResolvedValue({ data: result });
    render(<WagonReportModal scope={{ order: 366 }} onClose={vi.fn()} onSent={onSent} />);

    expect(mocks.get).toHaveBeenCalledWith("/loader/wagon-report/compose/?order=366");
    const field = await screen.findByLabelText("Текст отчёта");
    expect(field).toHaveValue(TEXT);
    expect(screen.getByText("Динаре · номер не указан — выберите чат в WhatsApp")).toBeInTheDocument();

    // Поправленный текст уходит в WhatsApp и в отметку.
    await user.type(field, "{end}\nОстальное завтра");
    await user.click(screen.getByRole("button", { name: "Отправить Динаре" }));

    const edited = `${TEXT}\nОстальное завтра`;
    expect(open).toHaveBeenCalledWith(`https://wa.me/?text=${encodeURIComponent(edited)}`, "_blank", "noopener");
    expect(mocks.post).toHaveBeenCalledWith("/loader/wagon-report/send/", {
      order_ids: [366],
      text: edited,
      delivery: "link",
      key: expect.stringMatching(/^[A-Za-z0-9_-]{8,64}$/),
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Открыли WhatsApp — отправьте сообщение Динаре");
    expect(onSent).toHaveBeenCalledWith(result);

    // Вкладку закрыли — открыть ещё раз можно, второй отметки нет.
    await user.click(screen.getByRole("button", { name: /Открыть WhatsApp ещё раз/ }));
    expect(open).toHaveBeenCalledTimes(2);
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });

  it("отметка не записалась после открытого WhatsApp — повтор только отмечает, WhatsApp второй раз не открывается", async () => {
    const user = userEvent.setup();
    const onSent = vi.fn();
    const result = sent();
    mocks.get.mockResolvedValue({ data: draft() });
    mocks.post.mockRejectedValueOnce(failure("Нет связи с сервером")).mockResolvedValueOnce({ data: result });
    render(<WagonReportModal scope={{ order: 366 }} onClose={vi.fn()} onSent={onSent} />);

    await user.click(await screen.findByRole("button", { name: "Отправить Динаре" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с сервером");
    expect(open).toHaveBeenCalledTimes(1);
    // Текст уже ушёл в WhatsApp — его не правят.
    expect(screen.getByLabelText("Текст отчёта")).toHaveAttribute("readonly");

    // Вкладку закрыли — открыть ещё раз можно отдельно, без отметки.
    await user.click(screen.getByRole("button", { name: /Открыть WhatsApp ещё раз/ }));
    expect(open).toHaveBeenCalledTimes(2);
    expect(mocks.post).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Отметить отправленным" }));

    expect(open).toHaveBeenCalledTimes(2);
    expect(mocks.post).toHaveBeenCalledTimes(2);
    expect(mocks.post.mock.calls[1][1]).toEqual(mocks.post.mock.calls[0][1]);
    expect(await screen.findByRole("status")).toHaveTextContent("Открыли WhatsApp — отправьте сообщение Динаре");
    expect(onSent).toHaveBeenCalledWith(result);
  });

  it("с номером открывает чат Динары", async () => {
    const user = userEvent.setup();
    mocks.get.mockResolvedValue({ data: draft({ recipient: { name: "Динара", to: "Динаре", phone: "77011234567" } }) });
    mocks.post.mockResolvedValue({ data: sent() });
    render(<WagonReportModal scope={{ order: 366 }} onClose={vi.fn()} onSent={vi.fn()} />);

    expect(await screen.findByText(/^Динаре · \+7/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Отправить Динаре" }));

    expect(open.mock.calls[0][0]).toMatch(/^https:\/\/wa\.me\/77011234567\?text=/);
  });

  it("отчёт за период уходит в очередь бота без вкладки WhatsApp", async () => {
    const user = userEvent.setup();
    const onSent = vi.fn();
    mocks.get.mockResolvedValue({
      data: draft({
        order_ids: [366, 367],
        delivery: "bot",
        recipient: { name: "Динара", to: "Динаре", phone: "", chat_name: "Отгрузка вагонов" },
      }),
    });
    mocks.post.mockResolvedValue({
      data: sent({ status: "queued", status_label: "В очереди", order_ids: [366, 367] }),
    });
    render(
      <WagonReportModal
        scope={{ date_from: "2026-09-18", date_to: "2026-09-24", search: "" }}
        onClose={vi.fn()}
        onSent={onSent}
      />,
    );

    expect(mocks.get).toHaveBeenCalledWith("/loader/wagon-report/compose/?date_from=2026-09-18&date_to=2026-09-24");
    expect(await screen.findByText("Динаре · в группу «Отгрузка вагонов»")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Отправить Динаре" }));

    expect(open).not.toHaveBeenCalled();
    expect(mocks.post.mock.calls[0][1]).toMatchObject({ order_ids: [366, 367], delivery: "bot" });
    expect(await screen.findByRole("status")).toHaveTextContent("В очереди — бот отправит Динаре");
    expect(screen.getByRole("button", { name: "Отправлено" })).toBeDisabled();
    expect(onSent).toHaveBeenCalledTimes(1);
  });

  it("ошибки — внутри окна; бота выключили — окно узнаёт, как отчёт уйдёт теперь", async () => {
    const user = userEvent.setup();
    const onSent = vi.fn();
    mocks.get
      .mockResolvedValueOnce({ data: draft({ delivery: "bot" }) })
      .mockResolvedValueOnce({ data: draft({ text: "другой текст" }) });
    mocks.post.mockRejectedValueOnce(
      failure("Бот сейчас не отправляет сообщения — отправьте отчёт через WhatsApp", "report_bot_unavailable"),
    );
    render(<WagonReportModal scope={{ order: 366 }} onClose={vi.fn()} onSent={onSent} />);

    await user.click(await screen.findByRole("button", { name: "Отправить Динаре" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Бот сейчас не отправляет сообщения");
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    // Текст, который человек видел (и мог поправить), остаётся.
    expect(screen.getByLabelText("Текст отчёта")).toHaveValue(TEXT);
    expect(await screen.findByText("Динаре · номер не указан — выберите чат в WhatsApp")).toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: "Отправить Динаре" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Скопировать/ })).toBeDisabled();
  });

  it("копирует текст отчёта", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    mocks.get.mockResolvedValue({ data: draft() });
    render(<WagonReportModal scope={{ order: 366 }} onClose={vi.fn()} onSent={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: /Скопировать/ }));

    expect(writeText).toHaveBeenCalledWith(TEXT);
    expect(screen.getByRole("button", { name: /Скопировано/ })).toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });
});
