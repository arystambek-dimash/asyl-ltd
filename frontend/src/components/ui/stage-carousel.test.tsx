import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StageCarousel } from "./stage-carousel";

class ResizeObserverMock {
  observe = vi.fn();
  disconnect = vi.fn();
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("StageCarousel", () => {
  it("makes controls in inactive slides inert and removes inert when activated", () => {
    const slides = [
      <button key="waiting" type="button">
        Ожидающий заказ
      </button>,
      <button key="loading" type="button">
        Заказ на погрузке
      </button>,
    ];
    const view = render(<StageCarousel active={0} slides={slides} slideKeys={["waiting", "loading"]} />);

    const waiting = screen.getByRole("button", { name: "Ожидающий заказ" });
    const loading = screen.getByRole("button", { name: "Заказ на погрузке", hidden: true });
    const waitingPane = waiting.parentElement;
    const loadingPane = loading.parentElement;

    expect(waitingPane).not.toHaveAttribute("inert");
    expect(waitingPane).not.toHaveAttribute("aria-hidden");
    expect(loadingPane).toHaveAttribute("inert");
    expect(loadingPane).toHaveAttribute("aria-hidden", "true");

    view.rerender(<StageCarousel active={1} slides={slides} slideKeys={["waiting", "loading"]} />);
    expect(waitingPane).toHaveAttribute("inert");
    expect(loadingPane).not.toHaveAttribute("inert");
  });
});
