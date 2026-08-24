import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { GrainToolbar } from "./grain-toolbar";

vi.mock("./live-scale-status", () => ({
  LiveScaleStatus: ({ active, scaleKey, label }: { active: boolean; scaleKey: "wagon" | "truck"; label: string }) =>
    active ? <div aria-label={`Весы ${label}`} data-scale-key={scaleKey} /> : null,
}));

function renderToolbar(overrides: Partial<React.ComponentProps<typeof GrainToolbar>> = {}) {
  const props = {
    canArrive: true,
    canSupply: true,
    canWeigh: true,
    onPassage: vi.fn(),
    onArrival: vi.fn(),
    onSupply: vi.fn(),
    ...overrides,
  };
  const result = render(<GrainToolbar {...props} />);
  return { ...result, props };
}

describe("GrainToolbar", () => {
  it("collects all allowed grain actions in one accessible dropdown", async () => {
    const user = userEvent.setup();
    renderToolbar();

    expect(screen.getByRole("group", { name: "Текущий вес" })).toBeInTheDocument();
    expect(screen.getByLabelText("Весы Вагоны")).toHaveAttribute("data-scale-key", "wagon");
    expect(screen.getByLabelText("Весы Вывоз")).toHaveAttribute("data-scale-key", "truck");
    await user.click(screen.getByRole("button", { name: "Операции с зерном" }));

    expect(screen.getByRole("menuitem", { name: "Оформить вывоз" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Принять поезд" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Новый приход" })).toBeInTheDocument();
  });

  it("preserves permission filtering and calls the selected action", async () => {
    const user = userEvent.setup();
    const { props } = renderToolbar({ canArrive: false, canWeigh: false });

    await user.click(screen.getByRole("button", { name: "Операции с зерном" }));
    expect(screen.queryByRole("menuitem", { name: "Оформить вывоз" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Принять поезд" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: "Новый приход" }));
    expect(props.onSupply).toHaveBeenCalledOnce();
    expect(props.onArrival).not.toHaveBeenCalled();
    expect(props.onPassage).not.toHaveBeenCalled();
  });

  it("shows only the scale when the employee can weigh but cannot create operations", () => {
    renderToolbar({ canArrive: false, canSupply: false });

    expect(screen.getByLabelText("Весы Вагоны")).toBeInTheDocument();
    expect(screen.getByLabelText("Весы Вывоз")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Операции с зерном" })).not.toBeInTheDocument();
  });

  it("renders nothing when no relevant permission is present", () => {
    const { container } = renderToolbar({ canArrive: false, canSupply: false, canWeigh: false });
    expect(container).toBeEmptyDOMElement();
  });
});
