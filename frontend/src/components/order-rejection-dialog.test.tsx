import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Order } from "@/lib/types";
import { OrderRejectionDialog } from "./order-rejection-dialog";

const post = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ api: { post }, apiError: (e: Error) => e.message }));
vi.mock("@/lib/toast", () => ({ showSuccess: vi.fn() }));
const order = { id: 42, client_name: "Тестовый клиент", status: "pending" } as Order;
beforeEach(() => {
  post.mockReset();
});

it("requires a reason, submits once and lets the parent refresh only on success", async () => {
  let finish!: (value: unknown) => void;
  post.mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const user = userEvent.setup();
  const done = vi.fn();
  render(<OrderRejectionDialog order={order} onClose={vi.fn()} onDone={done} />);
  const submit = screen.getByRole("button", { name: "Отклонить заявку" });
  expect(submit).toBeDisabled();
  await user.type(screen.getByRole("textbox", { name: "Причина отклонения" }), " Нет товара ");
  await user.dblClick(submit);
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith("/orders/42/reject/", { reason: "Нет товара" });
  expect(done).not.toHaveBeenCalled();
  finish({});
  await vi.waitFor(() => expect(done).toHaveBeenCalledOnce());
});

it("keeps the reason and shows a server error instead of closing", async () => {
  post.mockRejectedValue(new Error("У заявки уже изменился статус"));
  const user = userEvent.setup();
  const done = vi.fn();
  render(<OrderRejectionDialog order={order} onClose={vi.fn()} onDone={done} />);
  await user.type(screen.getByRole("textbox"), "Нет товара");
  await user.click(screen.getByRole("button", { name: "Отклонить заявку" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("У заявки уже изменился статус");
  expect(screen.getByRole("textbox")).toHaveValue("Нет товара");
  expect(done).not.toHaveBeenCalled();
});
