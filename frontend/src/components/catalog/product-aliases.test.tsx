import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Product } from "@/lib/types";
import { ProductAliasCodes, ProductAliasesEditor } from "./product-aliases";

const mocks = vi.hoisted(() => ({ post: vi.fn(), delete: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { post: mocks.post, delete: mocks.delete },
  apiError: (error: { response?: { data?: { detail?: string } }; message?: string }) =>
    error.response?.data?.detail ?? error.message,
}));

const product = (aliases: Product["aliases"] = []): Product => ({
  id: 5,
  name: "Мука высший сорт",
  weight_kg: "50.00",
  is_active: true,
  label: "Мука высший сорт · Красный 50 кг",
  aliases,
});

describe("ProductAliasesEditor", () => {
  beforeEach(() => {
    mocks.post.mockReset();
    mocks.delete.mockReset();
  });

  it("добавляет код по Enter и применяет товар из ответа", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const saved = product([{ id: 1, code: "Д1C" }]);
    mocks.post.mockResolvedValue({ data: saved });
    render(<ProductAliasesEditor product={product()} onChange={onChange} />);

    await user.type(screen.getByLabelText("Коды в отчётах о вагонах"), "д1с{Enter}");

    expect(mocks.post).toHaveBeenCalledWith("/products/5/aliases/", { code: "д1с", move: false });
    expect(onChange).toHaveBeenCalledWith(saved);
    expect(screen.getByLabelText("Коды в отчётах о вагонах")).toHaveValue("");
  });

  it("код другого товара переносит только после «Перенести»", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    mocks.post
      .mockRejectedValueOnce({
        response: {
          data: { code: "alias_taken", detail: "Код «Д1C» уже у товара «Мука первый сорт» — перенести его сюда?" },
        },
      })
      .mockResolvedValueOnce({ data: product([{ id: 1, code: "Д1C" }]) });
    render(<ProductAliasesEditor product={product()} onChange={onChange} />);

    await user.type(screen.getByLabelText("Коды в отчётах о вагонах"), "Д1с");
    await user.click(screen.getByRole("button", { name: /Добавить/ }));
    expect(await screen.findByText(/уже у товара «Мука первый сорт»/)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Перенести" }));

    expect(mocks.post).toHaveBeenLastCalledWith("/products/5/aliases/", { code: "Д1с", move: true });
    expect(onChange).toHaveBeenCalled();
  });

  it("убирает код и показывает ошибку внутри окна", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    mocks.delete.mockRejectedValueOnce(new Error("Нет прав")).mockResolvedValueOnce({ data: product() });
    render(<ProductAliasesEditor product={product([{ id: 1, code: "Д1C" }])} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Убрать код Д1C" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Нет прав");

    await user.click(screen.getByRole("button", { name: "Убрать код Д1C" }));
    expect(mocks.delete).toHaveBeenLastCalledWith("/products/5/aliases/1/");
    expect(onChange).toHaveBeenCalledWith(product());
  });
});

describe("ProductAliasCodes", () => {
  it("показывает коды или прочерк", () => {
    const { rerender } = render(<ProductAliasCodes aliases={[{ id: 1, code: "Д1C" }]} />);
    expect(screen.getByText("Д1C")).toBeInTheDocument();
    rerender(<ProductAliasCodes aliases={[]} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
