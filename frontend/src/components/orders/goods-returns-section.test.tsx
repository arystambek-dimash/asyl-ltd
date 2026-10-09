import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Department, GoodsReturn } from "@/lib/types";
import { GoodsReturnsSection } from "./goods-returns-section";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  me: { permissions: ["orders.view"] } as Record<string, unknown>,
}));

vi.mock("next/link", () => import("@/test-utils/next-link"));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me, loading: false }) }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: (...args: unknown[]) => mocks.get(...args), post: (...args: unknown[]) => mocks.post(...args) },
  apiError: () => "Ошибка",
  isCanceledRequest: () => false,
}));

const line = (fields: Partial<GoodsReturn["lines"][number]>): GoodsReturn["lines"][number] => ({
  order: 6055,
  order_department: "newcity",
  order_department_name: "Нью-Сити",
  product_label: "Мука 1 сорт",
  bags: 30,
  unit_price: "4700.00",
  amount: "141000.00",
  currency: "KZT",
  ...fields,
});

/** Принятый целиком возврат: строки по заказам есть, мука принята вся. */
const ACCEPTED = {
  status: "full",
  status_label: "Полностью возвращено",
  accepted_by_name: "Айдос",
  accepted_at: "2026-10-06T10:00:00+05:00",
} as const;

/** Сервер отдаёт новые возвраты первыми — список показывает их в том же порядке. */
const RETURNS: GoodsReturn[] = [
  {
    id: 13,
    created_at: "2026-10-06T09:30:00+05:00",
    client: 310,
    client_name: "ОсОО «Дан Агро Групп»",
    settlement: "cash",
    settlement_label: "Из кассы",
    warehouse_name: "Основной склад",
    created_by_name: "Иван Петров",
    ...ACCEPTED,
    items: [
      { id: 1, product_label: "Мука 1 сорт", bags: 20, accepted_bags: 20 },
      { id: 2, product_label: "Мука 2 сорт", bags: 20, accepted_bags: 20 },
    ],
    bags: 40,
    amounts: { KZT: "14000.00", USD: "60.00" },
    lines: [
      line({ order: 6060, product_label: "Мука 1 сорт", bags: 10, amount: "14000.00" }),
      line({ order: 6058, product_label: "Мука 2 сорт", bags: 20, amount: "40.00", currency: "USD" }),
      line({ order: 6058, product_label: "Мука 1 сорт", bags: 10, amount: "20.00", currency: "USD" }),
    ],
  },
  {
    id: 12,
    created_at: "2026-10-05T14:03:00+05:00",
    client: 7,
    client_name: "Нуржан Сарыагаш",
    settlement: "debt",
    settlement_label: "В счёт долга",
    warehouse_name: "Мельница",
    created_by_name: null,
    ...ACCEPTED,
    items: [{ id: 3, product_label: "Мука 1 сорт", bags: 30, accepted_bags: 30 }],
    bags: 30,
    amounts: { KZT: "141000.00" },
    lines: [line({})],
  },
];

const DEPARTMENTS = [
  { id: 1, code: "main", name: "Мельница", color: "#123456" },
  { id: 2, code: "newcity", name: "Нью-Сити", color: "#654321" },
] as Department[];

let results: GoodsReturn[] = RETURNS;

beforeEach(() => {
  results = RETURNS;
  mocks.me = { permissions: ["orders.view"] };
  mocks.get.mockReset();
  mocks.get.mockImplementation(async () => ({ data: { results, count: results.length, next: null, previous: null } }));
});

const requested = () => mocks.get.mock.calls.map(([raw]) => new URL(String(raw), "http://localhost"));

it("lists returns as the server orders them, with order links, money mode and both currencies", async () => {
  render(<GoodsReturnsSection departments={DEPARTMENTS} />);

  const table = await screen.findByRole("table");
  await within(table).findByText("Возврат №13");
  const [, newest, older] = within(table).getAllByRole("row");
  expect(newest).toHaveTextContent("Возврат №13");
  expect(older).toHaveTextContent("Возврат №12");

  // Строки одного заказа — одна ссылка с мешками; мука из разных заказов сведена.
  const newestRow = within(newest);
  expect(newestRow.getByRole("link", { name: "#6060 · 10 меш." })).toHaveAttribute(
    "href",
    "/orders/6060?back=%2Forders%3Ftab%3Dreturns",
  );
  expect(newestRow.getByRole("link", { name: "#6058 · 30 меш." })).toBeInTheDocument();
  expect(newest).toHaveTextContent("Мука 1 сорт · 20 мешков");
  expect(newest).toHaveTextContent("Мука 2 сорт · 20 мешков");
  expect(newest).toHaveTextContent("Всего 40 мешков");

  // ₸ и $ — два отдельных итога, без «+».
  expect(newestRow.getByText("Из кассы")).toBeInTheDocument();
  const money = newest.textContent!.replace(/\s/g, " ");
  expect(money).toContain("14 000 ₸");
  expect(money).toContain("60 $");
  expect(money).not.toContain("+");
  expect(within(older).getByText("В счёт долга")).toBeInTheDocument();
  expect(within(older).getByRole("link", { name: "#6055 · 30 меш." })).toBeInTheDocument();
  expect(older).toHaveTextContent("Мельница");
  expect(older).toHaveTextContent("—");

  // Телефонные карточки — те же возвраты в том же порядке.
  expect(screen.getAllByText(/^Возврат №\d+ ·/).map((node) => node.textContent?.split(" ·")[0])).toEqual([
    "Возврат №13",
    "Возврат №12",
  ]);
});

it("says there are no returns yet when the list is empty", async () => {
  results = [];
  render(<GoodsReturnsSection departments={DEPARTMENTS} />);

  expect((await screen.findAllByText("Возвратов пока нет.")).length).toBeGreaterThan(0);
});

it("sends search, dates and department to the server and starts the list over", async () => {
  const user = userEvent.setup();
  render(<GoodsReturnsSection departments={DEPARTMENTS} />);
  await waitFor(() => expect(requested()).toHaveLength(1));
  const [first] = requested();
  expect(first.pathname).toBe("/orders/returns/");
  expect(first.searchParams.get("page")).toBe("1");
  expect(first.searchParams.get("page_size")).toBe("50");
  expect(first.searchParams.has("search")).toBe(false);

  await user.type(screen.getByPlaceholderText("Поиск по клиенту, телефону или № заказа"), "#6055");
  await waitFor(() => expect(requested().at(-1)?.searchParams.get("search")).toBe("#6055"));

  await user.click(screen.getByRole("button", { name: /Дата/ }));
  const dates = screen.getByRole("dialog", { name: "Дата возврата" });
  const [from, to] = within(dates).getAllByDisplayValue("");
  fireEvent.change(from, { target: { value: "2026-10-01" } });
  fireEvent.change(to, { target: { value: "2026-10-05" } });
  await waitFor(() => expect(requested().at(-1)?.searchParams.get("date_to")).toBe("2026-10-05"));

  await user.click(screen.getByRole("button", { name: /Отдел/ }));
  await user.click(screen.getByRole("option", { name: "Нью-Сити" }));
  await waitFor(() => expect(requested().at(-1)?.searchParams.get("department")).toBe("newcity"));

  const last = requested().at(-1)!;
  expect(last.searchParams.get("search")).toBe("#6055");
  expect(last.searchParams.get("date_from")).toBe("2026-10-01");
  expect(last.searchParams.get("page")).toBe("1");
});

it("hides the department filter from staff pinned to a department", async () => {
  mocks.me = {
    permissions: ["orders.view"],
    sales_department: { id: 1, code: "main", name: "Мельница", color: "#123456" },
  };
  render(<GoodsReturnsSection departments={DEPARTMENTS} />);

  await screen.findByRole("table");
  expect(screen.queryByRole("button", { name: /Отдел/ })).not.toBeInTheDocument();
});

it("reloads the list after a new return is recorded", async () => {
  const { rerender } = render(<GoodsReturnsSection departments={DEPARTMENTS} refreshKey={0} />);
  await waitFor(() => expect(requested()).toHaveLength(1));

  rerender(<GoodsReturnsSection departments={DEPARTMENTS} refreshKey={1} />);
  await waitFor(() => expect(requested()).toHaveLength(2));
  expect(requested()[1].pathname).toBe("/orders/returns/");
});

/** Возврат, который менеджер создал, а кладовщик ещё не принял: строк по заказам и денег нет. */
const PENDING: GoodsReturn = {
  id: 20,
  created_at: "2026-10-09T09:00:00+05:00",
  client: 7,
  client_name: "Нуржан Сарыагаш",
  settlement: "debt",
  settlement_label: "В счёт долга",
  warehouse_name: "Мельница",
  created_by_name: "Иван Петров",
  status: "pending",
  status_label: "Ждёт приёмки",
  accepted_by_name: null,
  accepted_at: null,
  items: [
    { id: 31, product_label: "Первый сорт DIKHAN 50кг", bags: 16, accepted_bags: null },
    { id: 32, product_label: "Второй сорт KOROL 50кг", bags: 4, accepted_bags: null },
  ],
  bags: 0,
  amounts: {},
  lines: [],
};

it("shows the acceptance status: pending has no money yet, partial says how much was accepted", async () => {
  results = [
    PENDING,
    {
      ...PENDING,
      id: 19,
      status: "partial",
      status_label: "Частично возвращено",
      accepted_by_name: "Айдос",
      accepted_at: "2026-10-09T11:00:00+05:00",
      items: [{ id: 33, product_label: "Мука 1 сорт", bags: 16, accepted_bags: 15 }],
      bags: 15,
      amounts: { KZT: "70500.00" },
      lines: [line({ bags: 15, amount: "70500.00" })],
    },
    { ...PENDING, id: 18, status: "cancelled", status_label: "Отменён" },
  ];
  render(<GoodsReturnsSection departments={DEPARTMENTS} />);

  const table = await screen.findByRole("table");
  await within(table).findByText("Возврат №20");
  const [, pending, partial, cancelled] = within(table).getAllByRole("row");

  expect(within(pending).getByText("Ждёт приёмки")).toBeInTheDocument();
  expect(pending).toHaveTextContent("Первый сорт DIKHAN 50кг · 16 мешков");
  expect(pending).toHaveTextContent("Второй сорт KOROL 50кг · 4 мешка");
  expect(pending).toHaveTextContent("после приёмки");
  expect(pending.textContent).not.toMatch(/₸/);
  expect(within(pending).queryByRole("link")).toBeNull();

  expect(within(partial).getByText("Частично возвращено")).toBeInTheDocument();
  expect(partial).toHaveTextContent("Принято 15 из 16 мешков");
  expect(partial).toHaveTextContent("Принял Айдос");
  expect(partial.textContent!.replace(/\s/g, " ")).toContain("70 500 ₸");
  expect(within(partial).getByRole("link", { name: "#6055 · 15 меш." })).toBeInTheDocument();

  expect(within(cancelled).getByText("Отменён")).toBeInTheDocument();
  expect(cancelled).not.toHaveTextContent("после приёмки");
});

it("lets a manager with orders.edit cancel a pending return and applies the server row", async () => {
  const user = userEvent.setup();
  mocks.me = { permissions: ["orders.view", "orders.edit"] };
  results = [PENDING, RETURNS[0]];
  mocks.post.mockResolvedValueOnce({ data: { ...PENDING, status: "cancelled", status_label: "Отменён" } });
  render(<GoodsReturnsSection departments={DEPARTMENTS} />);

  const table = await screen.findByRole("table");
  await within(table).findByText("Возврат №20");
  // Принятый возврат не отменяется — только ждущий приёмки.
  expect(within(table).getAllByRole("button", { name: /Отменить возврат/ })).toHaveLength(1);
  await user.click(within(table).getByRole("button", { name: "Отменить возврат №20" }));

  const dialog = screen.getByRole("dialog", { name: "Отменить возврат №20?" });
  expect(dialog).toHaveTextContent("Долг, касса и склад не менялись");
  await user.click(within(dialog).getByRole("button", { name: "Отменить возврат" }));

  expect(mocks.post).toHaveBeenCalledWith("/orders/returns/20/cancel/", {});
  await waitFor(() => expect(within(table).getAllByText("Отменён").length).toBeGreaterThan(0));
  expect(within(table).queryByRole("button", { name: /Отменить возврат/ })).toBeNull();
  // Ответ применён без перечитывания списка.
  expect(requested()).toHaveLength(1);
});

it("hides the cancel action without orders.edit", async () => {
  results = [PENDING];
  render(<GoodsReturnsSection departments={DEPARTMENTS} />);

  await within(await screen.findByRole("table")).findByText("Возврат №20");
  expect(screen.queryByRole("button", { name: /Отменить возврат/ })).toBeNull();
});
