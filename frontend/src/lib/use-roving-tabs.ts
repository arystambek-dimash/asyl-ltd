"use client";

import { useId, useRef, type KeyboardEvent, type RefCallback } from "react";

type TabListProps = {
  role: "tablist";
  "aria-label": string;
  "aria-orientation": "horizontal";
};

type TabProps = {
  id: string;
  role: "tab";
  "aria-selected": boolean;
  "aria-controls": string;
  tabIndex: 0 | -1;
  ref: RefCallback<HTMLButtonElement>;
  onClick: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
};

type TabPanelProps = {
  id: string;
  role: "tabpanel";
  "aria-labelledby": string;
};

/**
 * Adds the ARIA relationships and roving keyboard focus expected from a
 * horizontal tab set while leaving the caller in full control of its markup
 * and visual styles.
 */
export function useRovingTabs<T extends string>({
  tabs,
  active,
  onChange,
  label,
}: {
  tabs: readonly T[];
  active: T;
  onChange: (tab: T) => void;
  label: string;
}) {
  const instanceId = useId();
  const tabRefs = useRef(new Map<T, HTMLButtonElement>());

  const tabId = (tab: T) => `${instanceId}-tab-${tab}`;
  const panelId = (tab: T) => `${instanceId}-panel-${tab}`;

  const tabListProps: TabListProps = {
    role: "tablist",
    "aria-label": label,
    "aria-orientation": "horizontal",
  };

  function getTabProps(tab: T): TabProps {
    return {
      id: tabId(tab),
      role: "tab",
      "aria-selected": active === tab,
      "aria-controls": panelId(tab),
      tabIndex: active === tab ? 0 : -1,
      ref: (node) => {
        if (node) tabRefs.current.set(tab, node);
        else tabRefs.current.delete(tab);
      },
      onClick: () => onChange(tab),
      onKeyDown: (event) => {
        const currentIndex = tabs.indexOf(tab);
        if (currentIndex < 0 || tabs.length < 2) return;

        let nextIndex: number | null = null;
        if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
        if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
        if (event.key === "Home") nextIndex = 0;
        if (event.key === "End") nextIndex = tabs.length - 1;
        if (nextIndex === null) return;

        const nextTab = tabs[nextIndex];
        if (!nextTab) return;
        event.preventDefault();
        onChange(nextTab);
        tabRefs.current.get(nextTab)?.focus();
      },
    };
  }

  function getTabPanelProps(tab: T): TabPanelProps {
    return {
      id: panelId(tab),
      role: "tabpanel",
      "aria-labelledby": tabId(tab),
    };
  }

  return { tabListProps, getTabProps, getTabPanelProps };
}
