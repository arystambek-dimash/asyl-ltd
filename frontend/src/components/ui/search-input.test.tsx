import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SearchInput } from "./search-input";

describe("SearchInput", () => {
  it("shows the clear button only when onClear is given and the field is not empty", () => {
    const onClear = vi.fn();
    const { rerender } = render(<SearchInput aria-label="Поиск" value="" onChange={() => {}} onClear={onClear} />);
    expect(screen.queryByRole("button", { name: "Очистить поиск" })).not.toBeInTheDocument();

    rerender(<SearchInput aria-label="Поиск" value="мука" onChange={() => {}} onClear={onClear} />);
    fireEvent.click(screen.getByRole("button", { name: "Очистить поиск" }));
    expect(onClear).toHaveBeenCalledOnce();

    rerender(<SearchInput aria-label="Поиск" value="мука" onChange={() => {}} />);
    expect(screen.queryByRole("button", { name: "Очистить поиск" })).not.toBeInTheDocument();
  });

  it("keeps the input classes and puts the wrapper classes on the wrapper", () => {
    render(
      <SearchInput
        aria-label="Поиск"
        size="lg"
        wrapperClassName="w-72"
        className="uppercase"
        value=""
        onChange={() => {}}
      />,
    );
    const input = screen.getByRole("textbox", { name: "Поиск" });
    expect(input).toHaveClass("h-12", "pl-10", "uppercase");
    expect(input.parentElement).toHaveClass("relative", "w-72");
  });
});
