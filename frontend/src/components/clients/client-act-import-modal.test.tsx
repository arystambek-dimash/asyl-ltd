import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { ActChunk, ActPreview } from "@/lib/client-acts";
import { ClientActImportModal } from "./client-act-import-modal";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { post },
  apiError: (e: Error) => e.message,
}));

const basePreview: ActPreview = {
  act: {
    number: "00000000509",
    period_start: "2026-01-01",
    period_end: "2026-10-07",
    counterparty: 'ОсОО "Дан Агро Групп"',
    currency: "USD",
    opening: "0.00",
    closing: "-5675.00",
    rows: 177,
  },
  name_matches: true,
  start: "2026-01-01",
  default_start: "2026-01-01",
  sales: { count: 171, total: "2100245.00" },
  payments: { count: 6, total: "2105920.00" },
  offsets: { count: 0, total: "0.00" },
  pending: "0.00",
  imported: 0,
  before_start: 0,
  opening_order: null,
  crm: {
    orders: 0,
    orders_total: "0.00",
    payments: 0,
    payments_total: "0.00",
    refunds: 0,
    refunds_total: "0.00",
    last_day: null,
  },
  balance_after: "-5675.00",
  problems: [],
};

let preview: ActPreview;
let chunks: (ActChunk | Error)[];

beforeEach(() => {
  preview = basePreview;
  chunks = [];
  post.mockReset().mockImplementation(async (_url: string, form: FormData) => {
    if (form.get("preview") === "true") return { data: preview };
    const next = chunks.shift();
    if (next instanceof Error) throw next;
    return { data: next };
  });
});

const actFile = () => new File(["xlsx"], "Акт сверки № 509.xlsx");

function sent(index: number) {
  const form = post.mock.calls[index][1] as FormData;
  return { preview: form.get("preview"), start: form.get("start"), file: (form.get("file") as File).name };
}

async function openWithFile() {
  const user = userEvent.setup();
  const onImported = vi.fn();
  const onClose = vi.fn();
  render(<ClientActImportModal clientId={42} open onClose={onClose} onImported={onImported} />);
  await user.upload(screen.getByLabelText("Файл акта сверки"), actFile());
  const dialog = screen.getByRole("dialog");
  await within(dialog).findByText("Дебет → заказы");
  return { user, dialog, onImported, onClose };
}

it("проверяет акт, показывает итог и переносит его порциями до конца", async () => {
  chunks = [
    { processed: 30, payments: 0, remaining: 147, finished: false, overpaid: null },
    { processed: 0, payments: 30, remaining: 147, finished: false, overpaid: null },
    { processed: 147, payments: 20, remaining: 0, finished: true, overpaid: "5675.00" },
  ];
  const { user, dialog, onImported, onClose } = await openWithFile();

  expect(post).toHaveBeenCalledWith("/clients/42/act-import/", expect.any(FormData), expect.anything());
  expect(sent(0)).toEqual({ preview: "true", start: null, file: "Акт сверки № 509.xlsx" });
  expect(dialog).toHaveTextContent("Акт № 00000000509 · 01.01.2026 – 07.10.2026 · USD");
  expect(dialog).toHaveTextContent("171 · 2 100 245 $");
  expect(dialog).toHaveTextContent("мы должны клиенту 5 675 $");

  await user.click(within(dialog).getByRole("button", { name: "Перенести 177 документов" }));

  await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
  // Деньги форматируются с неразрывными пробелами.
  expect(onImported.mock.calls[0][0].replace(/\s/g, " ")).toBe(
    "Акт № 00000000509 перенесён: 177 документов; мы должны клиенту 5 675 $",
  );
  expect(onClose).toHaveBeenCalled();
  expect([1, 2, 3].map((index) => sent(index).preview)).toEqual(["false", "false", "false"]);
  expect(sent(3).start).toBe("2026-01-01");
});

it("не даёт перенести акт с проблемами и показывает их в окне", async () => {
  preview = { ...basePreview, problems: ["Сальдо на начало 01.01.2026: по акту клиент должен 500 $, в CRM 0 $."] };
  const { dialog } = await openWithFile();

  expect(within(dialog).getByRole("alert")).toHaveTextContent("Сальдо на начало 01.01.2026");
  expect(within(dialog).getByRole("button", { name: /Перенести/ })).toBeDisabled();
});

it("при пересечении с CRM предлагает дату начала и пересчитывает по ней", async () => {
  preview = {
    ...basePreview,
    start: "2026-06-16",
    default_start: "2026-06-16",
    crm: {
      orders: 2,
      orders_total: "300.00",
      payments: 1,
      payments_total: "50.00",
      refunds: 1,
      refunds_total: "120.00",
      last_day: "2026-06-15",
    },
  };
  const { dialog } = await openWithFile();

  expect(dialog).toHaveTextContent(
    "уже есть 2 заказа на 300 $ и 1 оплата на 50 $, возвратов кассы 1 на 120 $ (последняя запись 15.06.2026)",
  );
  const date = within(dialog).getByLabelText("Переносить с");
  expect(date).toHaveValue("2026-06-16");
  fireEvent.change(date, { target: { value: "2026-03-01" } });

  await waitFor(() => expect(sent(post.mock.calls.length - 1).start).toBe("2026-03-01"));
});

it("обрыв переноса остаётся в окне и пересчитывает предпросмотр", async () => {
  chunks = [new Error("Сервер недоступен")];
  const { user, dialog, onImported } = await openWithFile();

  await user.click(within(dialog).getByRole("button", { name: "Перенести 177 документов" }));

  expect(await within(dialog).findByText("Сервер недоступен")).toBeInTheDocument();
  await waitFor(() => expect(sent(post.mock.calls.length - 1).preview).toBe("true"));
  expect(onImported).not.toHaveBeenCalled();
});

it("всё уже перенесено — переносить нечего", async () => {
  preview = {
    ...basePreview,
    sales: { count: 0, total: "0.00" },
    payments: { count: 0, total: "0.00" },
    imported: 177,
  };
  const { dialog } = await openWithFile();

  expect(dialog).toHaveTextContent("Все документы акта уже в CRM");
  expect(dialog).toHaveTextContent("Уже перенесено раньше177 документов");
  expect(within(dialog).getByRole("button", { name: "Перенести" })).toBeDisabled();
});

it("показывает зачёт по акту отдельной строкой и долг клиента словами", async () => {
  preview = {
    ...basePreview,
    act: { ...basePreview.act, number: "00000000517", currency: "KZT", closing: "324880.00" },
    sales: { count: 26, total: "1837250.00" },
    payments: { count: 27, total: "1512370.00" },
    offsets: { count: 26, total: "1212370.00" },
    balance_after: "324880.00",
  };
  const { dialog } = await openWithFile();

  expect(dialog).toHaveTextContent("Кредит → оплаты27 · 1 512 370 ₸");
  expect(dialog).toHaveTextContent("из них зачёт по акту (возвраты и др.)26 · 1 212 370 ₸");
  expect(dialog).toHaveTextContent("клиент должен 324 880 ₸");
});

it("нулевое сальдо — полностью оплачено", async () => {
  preview = { ...basePreview, act: { ...basePreview.act, closing: "0.00" }, balance_after: "0.00" };
  const { dialog } = await openWithFile();

  expect(within(dialog).getAllByText("полностью оплачено")).toHaveLength(2);
});

it("прерванный перенос можно довести: денег ещё не разнесено", async () => {
  preview = {
    ...basePreview,
    sales: { count: 0, total: "0.00" },
    payments: { count: 0, total: "0.00" },
    imported: 177,
    pending: "40.00",
  };
  chunks = [{ processed: 0, payments: 4, remaining: 0, finished: true, overpaid: "0.00" }];
  const { user, dialog, onImported } = await openWithFile();

  expect(dialog).toHaveTextContent("осталось разнести по заказам 40 $");
  await user.click(within(dialog).getByRole("button", { name: "Довести перенос" }));

  await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
});

it("не крутится вечно, если сервер не продвигает перенос", async () => {
  const stuck = { processed: 0, payments: 0, remaining: 3, finished: false, overpaid: null };
  chunks = [stuck, stuck, stuck];
  const { user, dialog, onImported } = await openWithFile();

  await user.click(within(dialog).getByRole("button", { name: "Перенести 177 документов" }));

  expect(await within(dialog).findByText(/Перенос не продвигается/)).toBeInTheDocument();
  expect(onImported).not.toHaveBeenCalled();
});
