import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { GrainToolbar } from "./grain-toolbar";

vi.mock("./live-scale-status", () => ({
  LiveScaleStatus: ({ active, scaleKey, label }: { active: boolean; scaleKey: "truck"; label: string }) =>
    active ? <div aria-label={`Весы ${label}`} data-scale-key={scaleKey} /> : null,
}));

function renderToolbar(overrides: Partial<React.ComponentProps<typeof GrainToolbar>> = {}) {
  const props = {
    direction: "intake" as const,
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
  it("shows only intake operations in the intake segment", async () => {
    const user = userEvent.setup();
    renderToolbar();

    expect(screen.queryByLabelText(/^Весы /)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Оформить вывоз" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Операции прихода" }));
    expect(screen.getByRole("menuitem", { name: "Принять поезд" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Новый приход" })).toBeInTheDocument();
  });

  it("shows only the truck scale and export button in the export segment", async () => {
    const user = userEvent.setup();
    const { props } = renderToolbar({ direction: "passage" });

    expect(screen.getByRole("group", { name: "Текущий вес вывоза" })).toBeInTheDocument();
    expect(screen.getByLabelText("Весы Вывоз")).toHaveAttribute("data-scale-key", "truck");
    expect(screen.queryByRole("button", { name: "Операции прихода" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Оформить вывоз" }));
    expect(props.onPassage).toHaveBeenCalledOnce();
  });

  it("preserves permission filtering and calls the selected action", async () => {
    const user = userEvent.setup();
    const { props } = renderToolbar({ canArrive: false, canWeigh: false });

    expect(screen.queryByRole("button", { name: "Оформить вывоз" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Операции прихода" }));
    expect(screen.queryByRole("menuitem", { name: "Принять поезд" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: "Новый приход" }));
    expect(props.onSupply).toHaveBeenCalledOnce();
    expect(props.onArrival).not.toHaveBeenCalled();
    expect(props.onPassage).not.toHaveBeenCalled();
  });

  it("shows only the truck scale in export when the employee cannot create a trip", () => {
    renderToolbar({ direction: "passage", canArrive: false, canSupply: false });

    expect(screen.getByLabelText("Весы Вывоз")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Оформить вывоз" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Операции прихода" })).not.toBeInTheDocument();
  });

  it("renders nothing when no relevant permission is present", () => {
    const intake = renderToolbar({ canArrive: false, canSupply: false, canWeigh: true });
    expect(intake.container).toBeEmptyDOMElement();
    intake.unmount();

    const passage = renderToolbar({ direction: "passage", canArrive: false, canSupply: true, canWeigh: false });
    expect(passage.container).toBeEmptyDOMElement();
  });
});
