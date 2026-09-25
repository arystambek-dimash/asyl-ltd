import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { PortalOrder } from "@/lib/types";

import PortalOrdersPage from "./page";

const mocks = vi.hoisted(() => ({ useApi: vi.fn() }));

vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("next/link", () => import("@/test-utils/next-link"));

function order(id: number): PortalOrder {
  return { id, status: "confirmed", currency: "KZT", total_amount: null, paid_total: null } as PortalOrder;
}

function respond(data: PortalOrder[] | null, loading = false) {
  mocks.useApi.mockReturnValue({ data, loading, error: "", reload: vi.fn() });
}

beforeEach(() => mocks.useApi.mockReset());

it("склоняет счётчик заказов", () => {
  respond([order(1)]);
  const { rerender } = render(<PortalOrdersPage />);
  expect(screen.getByText("1 заказ")).toBeInTheDocument();

  respond([order(1), order(2)]);
  rerender(<PortalOrdersPage />);
  expect(screen.getByText("2 заказа")).toBeInTheDocument();
});

it("не показывает «0 заказов», пока список грузится", () => {
  respond(null, true);
  render(<PortalOrdersPage />);
  expect(screen.queryByText(/^\d+ заказ(а|ов)?$/)).not.toBeInTheDocument();
});
