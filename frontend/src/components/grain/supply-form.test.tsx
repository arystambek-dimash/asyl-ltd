import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GrainSilo, GrainSiloType } from "@/lib/types";
import { SupplyForm } from "./supply-form";

const mocks = vi.hoisted(() => ({
  types: [] as GrainSiloType[],
  silos: [] as GrainSilo[],
  reloadTypes: vi.fn(),
  post: vi.fn(),
}));

vi.mock("@/lib/use-api", () => ({
  useApi: (url: string) =>
    url === "/grain/silo-types/"
      ? { data: mocks.types, loading: false, error: "", reload: mocks.reloadTypes }
      : { data: mocks.silos, loading: false, error: "", reload: vi.fn() },
}));
vi.mock("@/lib/api", () => ({ api: { post: mocks.post }, apiError: () => "ошибка" }));

const grainType = (id: number, name: string) => ({ id, name }) as GrainSiloType;
const silo = (id: number, name: string, siloType: number) =>
  ({ id, name, silo_type: siloType, status: "active", free_capacity_kg: 100_000 }) as GrainSilo;

describe("SupplyForm", () => {
  beforeEach(() => {
    mocks.types = [grainType(1, "Пшеница")];
    mocks.silos = [silo(5, "Силос 5", 1), silo(6, "Силос 6", 2)];
    mocks.reloadTypes.mockReset().mockImplementation(async () => {
      mocks.types = [grainType(1, "Пшеница"), grainType(2, "Ячмень")];
    });
    mocks.post.mockReset().mockResolvedValue({ data: grainType(2, "Ячмень") });
  });

  it("сбрасывает силос прежнего типа, когда тип создали прямо в форме", async () => {
    const user = userEvent.setup();
    render(<SupplyForm onDone={vi.fn()} onCancel={vi.fn()} />);

    await user.type(screen.getByLabelText("Название поставщика *"), "ТОО Колос");
    await user.selectOptions(screen.getByLabelText("Тип зерна *"), "1");
    await user.type(screen.getByLabelText("Ожидаемый вес, тонн *"), "68");
    await user.selectOptions(screen.getByLabelText("Силос назначения *"), "5");
    expect(screen.getByRole("button", { name: /Создать приход/ })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "+ Создать новый тип" }));
    await user.type(screen.getByPlaceholderText("Пшеница продовольственная"), "Ячмень");
    await user.click(screen.getByRole("button", { name: /Создать тип/ }));

    await waitFor(() => expect(screen.getByLabelText("Тип зерна *")).toHaveValue("2"));
    // Силос 5 — под пшеницу: с ним бэкенд ответит «Тип зерна не совпадает с типом силоса».
    expect(screen.getByLabelText("Силос назначения *")).toHaveValue("");
    expect(screen.getByRole("button", { name: /Создать приход/ })).toBeDisabled();
  });
});
