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

/**
 * Возвраты по старым правилам (с деньгами по заказам): в истории как были.
 * Сервер отдаёт новые возвраты первыми — список показывает их в том же порядке.
 */
const RETURNS: GoodsReturn[] = [
  {
    id: 13,
    created_at: "2026-10-06T09:30:00+05:00",
    client_name: "ОсОО «Дан Агро Групп»",
    settlement_label: "Из кассы",
    warehouse_name: "Основной склад",
    created_by_name: "Иван Петров",
    ...ACCEPTED,
    items: [
      { id: 1, product_label: "Мука 1 сорт", bags: 20, accepted_bags: 20 },
      { id: 2, product_label: "Мука 2 сорт", bags: 20, accepted_bags: 20 },
    ],
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
    client_name: "Нуржан Сарыагаш",
    settlement_label: "В счёт долга",
    warehouse_name: "Мельница",
    created_by_name: null,
    ...ACCEPTED,
    items: [{ id: 3, product_label: "Мука 1 сорт", bags: 30, accepted_bags: 30 }],
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

it("keeps legacy returns as they were: order links, money mode and both currencies", async () => {
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

/** Возврат, который менеджер создал, а кладовщик ещё не принял: с заказами и деньгами не связан. */
const PENDING: GoodsReturn = {
  id: 20,
  created_at: "2026-10-09T09:00:00+05:00",
  client_name: "Нуржан Сарыагаш",
  settlement_label: null,
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
  amounts: {},
  lines: [],
};

it("shows new returns by status: requested, then accepted per product — no orders, no money", async () => {
  results = [
    PENDING,
    {
      ...PENDING,
      id: 19,
      status: "partial",
      status_label: "Частично возвращено",
      accepted_by_name: "Айдос",
      accepted_at: "2026-10-09T11:00:00+05:00",
      items: [
        { id: 33, product_label: "Первый сорт DIKHAN 50кг", bags: 16, accepted_bags: 15 },
        { id: 34, product_label: "Второй сорт KOROL 50кг", bags: 4, accepted_bags: 4 },
      ],
    },
    {
      ...PENDING,
      id: 17,
      status: "full",
      status_label: "Полностью возвращено",
      accepted_by_name: "Айдос",
      accepted_at: "2026-10-09T10:00:00+05:00",
      items: [{ id: 35, product_label: "Высший сорт OMAD 50кг", bags: 7, accepted_bags: 7 }],
    },
    { ...PENDING, id: 18, status: "cancelled", status_label: "Отменён" },
  ];
  render(<GoodsReturnsSection departments={DEPARTMENTS} />);

  const table = await screen.findByRole("table");
  await within(table).findByText("Возврат №20");
  const [, pending, partial, full, cancelled] = within(table).getAllByRole("row");

  expect(within(pending).getByText("Ждёт приёмки")).toBeInTheDocument();
  expect(pending).toHaveTextContent("Первый сорт DIKHAN 50кг · 16 мешков");
  expect(pending).toHaveTextContent("Второй сорт KOROL 50кг · 4 мешка");
  expect(pending).not.toHaveTextContent("после приёмки");

  expect(within(partial).getByText("Частично возвращено")).toBeInTheDocument();
  expect(partial).toHaveTextContent("Первый сорт DIKHAN 50кг · 15 мешков");
  expect(partial).toHaveTextContent("Второй сорт KOROL 50кг · 4 мешка");
  expect(partial).toHaveTextContent("Принято 19 из 20 мешков");
  expect(partial).toHaveTextContent("Принял Айдос");

  expect(within(full).getByText("Полностью возвращено")).toBeInTheDocument();
  expect(full).toHaveTextContent("Высший сорт OMAD 50кг · 7 мешков");

  expect(within(cancelled).getByText("Отменён")).toBeInTheDocument();

  // Новый возврат не связан с заказами и деньгами: ни ссылок на заказы, ни сумм, ни способа расчёта.
  for (const row of [pending, partial, full, cancelled]) {
    expect(within(row).queryByRole("link")).toBeNull();
    expect(row.textContent).not.toMatch(/₸|\$|В счёт долга|Из кассы/);
  }
});

it("shows new and legacy returns side by side: money only on the legacy one", async () => {
  results = [
    {
      ...PENDING,
      id: 21,
      status: "full",
      status_label: "Полностью возвращено",
      accepted_by_name: "Айдос",
      accepted_at: "2026-10-09T10:00:00+05:00",
      items: [{ id: 36, product_label: "Мука 1 сорт", bags: 5, accepted_bags: 5 }],
    },
    RETURNS[1],
  ];
  render(<GoodsReturnsSection departments={DEPARTMENTS} />);

  const table = await screen.findByRole("table");
  await within(table).findByText("Возврат №21");
  const [, fresh, legacy] = within(table).getAllByRole("row");

  expect(fresh).toHaveTextContent("Мука 1 сорт · 5 мешков");
  expect(fresh.textContent).not.toMatch(/₸|В счёт долга/);
  expect(within(fresh).queryByRole("link")).toBeNull();

  expect(within(legacy).getByText("В счёт долга")).toBeInTheDocument();
  expect(legacy.textContent!.replace(/\s/g, " ")).toContain("141 000 ₸");
  expect(within(legacy).getByRole("link", { name: "#6055 · 30 меш." })).toBeInTheDocument();

  // Телефон: у нового возврата блока денег нет, у старого — способ расчёта и сумма.
  const card = (id: number) => screen.getByText(new RegExp(`^Возврат №${id} ·`)).closest("li")!;
  const [freshCard, legacyCard] = [card(21), card(12)];
  expect(freshCard.textContent).not.toMatch(/₸|В счёт долга/);
  expect(legacyCard).toHaveTextContent("В счёт долга");
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
  expect(dialog).toHaveTextContent("Склад не менялся");
  expect(dialog).not.toHaveTextContent(/долг|касс/i);
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
