import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { authTokenStorageKey } from "@/lib/api";
import { AppShell } from "./app-shell";

const mocks = vi.hoisted(() => ({
  loadMe: vi.fn(),
  logout: vi.fn(),
  refreshMe: vi.fn(),
  replace: vi.fn(),
  syncExternalSession: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
  useRouter: () => ({ replace: mocks.replace }),
}));

vi.mock("@/store/auth", () => ({
  useAuth: () => ({
    me: { is_client: false, is_monoblock: false },
    loading: false,
    loadMe: mocks.loadMe,
    logout: mocks.logout,
    refreshMe: mocks.refreshMe,
    syncExternalSession: mocks.syncExternalSession,
  }),
}));

vi.mock("@/components/onboarding-tour", () => ({ OnboardingTour: () => null }));
vi.mock("./sidebar", () => ({ Sidebar: () => null }));
vi.mock("./topbar", () => ({ Topbar: () => null }));

describe("AppShell cross-tab authentication", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("synchronizes replacements and logs out only when refresh is removed", () => {
    render(<AppShell title="Dashboard">content</AppShell>);

    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: authTokenStorageKey("refresh"),
          newValue: "rotated-refresh",
          storageArea: localStorage,
        }),
      );
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: authTokenStorageKey("access"),
          newValue: null,
          storageArea: localStorage,
        }),
      );
    });
    expect(mocks.syncExternalSession).toHaveBeenCalledTimes(1);
    expect(mocks.logout).not.toHaveBeenCalled();

    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: authTokenStorageKey("refresh"),
          oldValue: "refresh",
          newValue: null,
          storageArea: localStorage,
        }),
      );
    });

    expect(mocks.logout).toHaveBeenCalledTimes(1);
  });
});
