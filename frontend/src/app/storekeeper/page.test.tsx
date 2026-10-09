import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StorekeeperReturn } from "@/lib/types";

import StorekeeperPage from "./page";

const mocks = vi.hoisted(() => ({
  permissions: [] as string[],
  get: vi.fn(),
  post: vi.fn(),
  polls: [] as { poll: () => Promise<unknown>; active: boolean }[],
}));

vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { id: 1, is_superuser: false, permissions: mocks.permissions }, loading: false }),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: mocks.get, post: mocks.post },
  apiError: (error: Error) => error.message,
  isCanceledRequest: () => false,
}));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (poll: () => Promise<unknown>, _interval: number, active = true) => {
    mocks.polls.push({ poll, active });
  },
}));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("@/components/require-perm", () => import("@/test-utils/require-perm"));

/** Возврат, созданный менеджером: две муки, кладовщик ещё ничего не проверил. */
function makeReturn(fields: Partial<StorekeeperReturn> = {}): StorekeeperReturn {
  return {
    id: 21,
    created_at: "2026-10-09T09:15:00+05:00",
    client_name: "Нуржан Сарыагаш",
    warehouse_name: "Мельница",
    created_by_name: "Иван Петров",
    status: "pending",
    status_label: "Ждёт приёмки",
    accepted_by_name: null,
    accepted_at: null,
    bags: 20,
    accepted_bags: null,
    items: [
      { id: 31, product_label: "Первый сорт DIKHAN 50кг", bags: 16, accepted_bags: null },
      { id: 32, product_label: "Второй сорт KOROL 50кг", bags: 4, accepted_bags: null },
    ],
    ...fields,
  };
}

/** Тот же возврат с проверенными строками: принятое — по порядку строк. */
function checked(first: number | null, second: number | null, fields: Partial<StorekeeperReturn> = {}) {
  const base = makeReturn();
  const accepted = [first, second];
  return makeReturn({
    items: base.items.map((item, index) => ({ ...item, accepted_bags: accepted[index] })),
    accepted_bags: first === null && second === null ? null : (first ?? 0) + (second ?? 0),
    ...fields,
  });
}

let pending: StorekeeperReturn[] = [];
let closedRows: StorekeeperReturn[] = [];

const requested = () => mocks.get.mock.calls.map(([raw]) => new URL(String(raw), "http://localhost"));
const footer = () => screen.getByTestId("footer");
const line = (label: string) => screen.getByRole("listitem", { name: label });

async function openReturn(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /Нуржан Сарыагаш/ }));
  return screen.getByRole("list", { name: "Проверка по списку" });
}

beforeEach(() => {
  mocks.permissions = ["storekeeper.view", "storekeeper.confirm"];
  mocks.polls = [];
  pending = [makeReturn()];
  closedRows = [];
  mocks.post.mockReset();
  mocks.get.mockReset().mockImplementation(async (raw: string) => {
    const results = raw.includes("state=closed") ? closedRows : pending;
    return { data: { results, count: results.length, next: null, previous: null } };
  });
});

describe("Кладовщик", () => {
  it("показывает возвраты на приёмку без денег: клиент крупно, откуда и кто создал", async () => {
    render(<StorekeeperPage />);

    const card = await screen.findByRole("button", { name: /Нуржан Сарыагаш/ });
    expect(card).toHaveTextContent("Возврат №21 · Мельница · Иван Петров");
    expect(card).toHaveTextContent("Первый сорт DIKHAN 50кг16 мешков");
    expect(card).toHaveTextContent("Итого 20 мешков");
    expect(screen.getByRole("tab", { name: "Ждут приёмки, 1" })).toHaveAttribute("aria-selected", "true");
    expect(document.body.textContent).not.toMatch(/₸|\$|долг|касс/i);

    const [first] = requested();
    expect(first.pathname).toBe("/storekeeper/returns/");
    expect(first.searchParams.get("state")).toBe("pending");
    expect(first.searchParams.get("page")).toBe("1");
  });

  it("«Подтвердить» принимает строку целиком, закрыть можно только после всех строк", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValueOnce({ data: checked(16, null) });
    render(<StorekeeperPage />);

    await openReturn(user);
    expect(within(footer()).getByRole("button", { name: "Закрыть возврат" })).toBeDisabled();
    expect(footer()).toHaveTextContent("Подтвердите все строки (0 из 2)");

    await user.click(within(line("Первый сорт DIKHAN 50кг")).getByRole("button", { name: "Подтвердить" }));

    expect(mocks.post).toHaveBeenCalledWith("/storekeeper/returns/21/items/31/", { accepted_bags: 16 });
    expect(await within(line("Первый сорт DIKHAN 50кг")).findByText("принято 16 из 16")).toBeInTheDocument();
    expect(within(line("Первый сорт DIKHAN 50кг")).queryByRole("button", { name: "Подтвердить" })).toBeNull();
    expect(screen.getByText("Проверено 1 из 2")).toBeInTheDocument();
    expect(within(footer()).getByRole("button", { name: "Закрыть возврат" })).toBeDisabled();
    expect(footer()).toHaveTextContent("Подтвердите все строки (1 из 2)");
  });

  it("«Меньше?» — кладовщик набирает, сколько пришло; закрытие частичное, возврат уходит в историю", async () => {
    const user = userEvent.setup();
    pending = [checked(16, null)];
    const partial = checked(16, 3);
    mocks.post.mockResolvedValueOnce({ data: partial }).mockResolvedValueOnce({
      data: {
        ...partial,
        status: "partial",
        status_label: "Частично возвращено",
        accepted_by_name: "Айдос",
        accepted_at: "2026-10-09T10:00:00+05:00",
      },
    });
    render(<StorekeeperPage />);

    await openReturn(user);
    const korol = line("Второй сорт KOROL 50кг");
    await user.click(within(korol).getByRole("button", { name: "Меньше?" }));
    const count = within(korol).getByLabelText(/Сколько мешков пришло/);
    expect(count).toHaveValue("4");
    await user.click(within(korol).getByRole("button", { name: "На мешок меньше" }));
    expect(count).toHaveValue("3");
    await user.click(within(korol).getByRole("button", { name: "Принять 3 мешка" }));

    expect(mocks.post).toHaveBeenLastCalledWith("/storekeeper/returns/21/items/32/", { accepted_bags: 3 });
    expect(await within(line("Второй сорт KOROL 50кг")).findByText("принято 3 из 4")).toBeInTheDocument();
    expect(within(line("Второй сорт KOROL 50кг")).getByRole("button", { name: "Изменить" })).toBeInTheDocument();
    expect(footer()).toHaveTextContent("Принято 19 из 20 мешков — частично");

    await user.click(within(footer()).getByRole("button", { name: "Закрыть возврат" }));
    const dialog = screen.getByRole("dialog", { name: "Закрыть возврат №21?" });
    expect(dialog).toHaveTextContent("Принято 19 из 20 мешков — частично");
    expect(dialog).toHaveTextContent("лягут на склад «Мельница»");
    await user.click(within(dialog).getByRole("button", { name: "Закрыть возврат" }));

    expect(mocks.post).toHaveBeenLastCalledWith("/storekeeper/returns/21/close/", {});
    const done = await screen.findByText("Частично возвращено");
    expect(done.parentElement).toHaveTextContent("Возврат №21 закрыт");
    expect(done.parentElement).toHaveTextContent("Принято 19 из 20 мешков");

    await user.click(screen.getByRole("button", { name: "К списку возвратов" }));
    expect(await screen.findByText("Возвратов на приёмку нет")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Ждут приёмки, 0" })).toBeInTheDocument();
  });

  it("пока в строке набрано, но не принято число, закрыть возврат нельзя", async () => {
    const user = userEvent.setup();
    pending = [checked(16, 4)];
    mocks.post.mockResolvedValueOnce({ data: checked(16, 3) });
    render(<StorekeeperPage />);

    await openReturn(user);
    const close = within(footer()).getByRole("button", { name: "Закрыть возврат" });
    expect(close).toBeEnabled();
    const korol = line("Второй сорт KOROL 50кг");
    await user.click(within(korol).getByRole("button", { name: "Меньше?" }));
    await user.click(within(korol).getByRole("button", { name: "На мешок меньше" }));

    expect(within(korol).getByLabelText(/Сколько мешков пришло/)).toHaveValue("3");
    expect(close).toBeDisabled();
    expect(footer()).toHaveTextContent("Примите или отмените набранное число: «Второй сорт KOROL 50кг»");
    await user.click(within(korol).getByRole("button", { name: "Отмена" }));
    expect(close).toBeEnabled();
    expect(footer()).toHaveTextContent("Принято 20 из 20 мешков — полностью");

    await user.click(within(korol).getByRole("button", { name: "Меньше?" }));
    await user.click(within(korol).getByRole("button", { name: "На мешок меньше" }));
    await user.click(within(korol).getByRole("button", { name: "Принять 3 мешка" }));

    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post).toHaveBeenLastCalledWith("/storekeeper/returns/21/items/32/", { accepted_bags: 3 });
    expect(await within(footer()).findByText("Принято 19 из 20 мешков — частично")).toBeInTheDocument();
    expect(close).toBeEnabled();
  });

  it("больше привезённого принять нельзя", async () => {
    const user = userEvent.setup();
    render(<StorekeeperPage />);

    await openReturn(user);
    const korol = line("Второй сорт KOROL 50кг");
    await user.click(within(korol).getByRole("button", { name: "Меньше?" }));
    const count = within(korol).getByLabelText(/Сколько мешков пришло/);
    await user.clear(count);
    await user.type(count, "5");

    expect(within(korol).getByText("Привезли 4 мешка — больше принять нельзя")).toBeInTheDocument();
    expect(within(korol).getByRole("button", { name: /^Принять/ })).toBeDisabled();
    expect(within(korol).getByRole("button", { name: "На мешок больше" })).toBeDisabled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("ничего не принято — закрытие отменит возврат и склад не тронет", async () => {
    const user = userEvent.setup();
    pending = [checked(0, 0)];
    render(<StorekeeperPage />);

    await openReturn(user);
    expect(footer()).toHaveTextContent("Ничего не принято — возврат будет отменён");
    await user.click(within(footer()).getByRole("button", { name: "Закрыть возврат" }));
    expect(screen.getByRole("dialog", { name: "Закрыть возврат №21?" })).toHaveTextContent("Склад не изменится");
  });

  it("отказ закрытия виден в окне и остаётся на экране после него", async () => {
    const user = userEvent.setup();
    pending = [checked(16, 4)];
    const refusal =
      "Возврат больше не помещается: клиент уже погасил долг. Пусть менеджер отменит его и создаст заново";
    mocks.post.mockRejectedValueOnce(new Error(refusal));
    render(<StorekeeperPage />);

    await openReturn(user);
    expect(footer()).toHaveTextContent("Принято 20 из 20 мешков — полностью");
    await user.click(within(footer()).getByRole("button", { name: "Закрыть возврат" }));
    const dialog = screen.getByRole("dialog", { name: "Закрыть возврат №21?" });
    await user.click(within(dialog).getByRole("button", { name: "Закрыть возврат" }));

    expect(await within(dialog).findByText(refusal)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(screen.getByRole("alert")).toHaveTextContent(refusal);
    expect(screen.getByRole("list", { name: "Проверка по списку" })).toBeInTheDocument();
  });

  it("возврат, закрытый с другого устройства, уводит к списку с пояснением", async () => {
    const user = userEvent.setup();
    render(<StorekeeperPage />);

    await openReturn(user);
    pending = [];
    await act(() =>
      mocks.polls
        .filter((tick) => tick.active)
        .at(-1)!
        .poll(),
    );

    expect(await screen.findByText(/Возврат №21 уже не ждёт приёмки/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Проверка по списку" })).toBeNull();
  });

  it("история: закрытые возвраты со статусом и кто принял, без кнопок приёмки", async () => {
    const user = userEvent.setup();
    closedRows = [
      checked(16, 4, {
        id: 17,
        status: "full",
        status_label: "Полностью возвращено",
        accepted_by_name: "Айдос",
        accepted_at: "2026-10-08T15:10:00+05:00",
      }),
    ];
    render(<StorekeeperPage />);

    await user.click(await screen.findByRole("tab", { name: "История" }));

    expect(await screen.findByText("Полностью возвращено")).toBeInTheDocument();
    expect(screen.getByText("Принято 20 из 20 мешков")).toBeInTheDocument();
    expect(screen.getByText(/Принял Айдос/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Нуржан Сарыагаш/ })).toBeNull();
    await waitFor(() => expect(requested().at(-1)?.searchParams.get("state")).toBe("closed"));
  });

  it("история: отменённый менеджером возврат — ничего не принято, кто отменил", async () => {
    const user = userEvent.setup();
    // Кладовщик успел подтвердить одну муку, потом менеджер отменил возврат.
    closedRows = [
      checked(16, null, {
        id: 17,
        status: "cancelled",
        status_label: "Отменён",
        accepted_by_name: "Иван Петров",
        accepted_at: "2026-10-08T15:10:00+05:00",
      }),
    ];
    render(<StorekeeperPage />);

    await user.click(await screen.findByRole("tab", { name: "История" }));

    const badge = await screen.findByText("Отменён");
    const card = badge.closest("div.rounded-2xl") as HTMLElement;
    expect(card).toHaveTextContent("Первый сорт DIKHAN 50кг16 мешков");
    expect(card).toHaveTextContent("Ничего не принято");
    expect(card).toHaveTextContent("Отменил Иван Петров");
    expect(card).not.toHaveTextContent(/16 из 16|Принято|Закрыл/);
  });

  it("без права приёмки экран только для просмотра", async () => {
    const user = userEvent.setup();
    mocks.permissions = ["storekeeper.view"];
    render(<StorekeeperPage />);

    await openReturn(user);
    expect(screen.queryByRole("button", { name: "Подтвердить" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Меньше?" })).toBeNull();
    expect(footer()).toBeEmptyDOMElement();
  });
});
