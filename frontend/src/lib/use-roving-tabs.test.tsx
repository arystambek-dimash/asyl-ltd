import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useRovingTabs } from "./use-roving-tabs";

const views = ["live", "analytics", "archive"] as const;

function TabsHarness() {
  const [active, setActive] = useState<(typeof views)[number]>("live");
  const tabs = useRovingTabs({
    tabs: views,
    active,
    onChange: setActive,
    label: "Режим камеры",
  });

  return (
    <>
      <div {...tabs.tabListProps}>
        {views.map((view) => (
          <button key={view} type="button" {...tabs.getTabProps(view)}>
            {view}
          </button>
        ))}
      </div>
      <div {...tabs.getTabPanelProps(active)}>{active} panel</div>
    </>
  );
}

describe("useRovingTabs", () => {
  it("links each tab to its active panel and keeps only the active tab tabbable", () => {
    render(<TabsHarness />);

    const live = screen.getByRole("tab", { name: "live" });
    const analytics = screen.getByRole("tab", { name: "analytics" });
    const panel = screen.getByRole("tabpanel");

    expect(screen.getByRole("tablist", { name: "Режим камеры" })).toHaveAttribute("aria-orientation", "horizontal");
    expect(live).toHaveAttribute("aria-selected", "true");
    expect(live).toHaveAttribute("tabindex", "0");
    expect(analytics).toHaveAttribute("tabindex", "-1");
    expect(live).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", live.id);
  });

  it("activates and focuses tabs with arrows, Home and End", async () => {
    const user = userEvent.setup();
    render(<TabsHarness />);

    const live = screen.getByRole("tab", { name: "live" });
    live.focus();

    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "archive" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "archive" })).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{Home}");
    expect(live).toHaveFocus();

    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "archive" })).toHaveFocus();

    await user.keyboard("{ArrowRight}");
    expect(live).toHaveFocus();
    expect(live).toHaveAttribute("aria-selected", "true");
  });
});
