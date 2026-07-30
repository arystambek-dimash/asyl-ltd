import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authTokenStorageKey } from "@/lib/api";
import { AppShell } from "./app-shell";

const mocks = vi.hoisted(() => ({
  auth: {
    me: { is_client: false, is_monoblock: false } as { is_client: boolean; is_monoblock: boolean } | null,
    loading: false,
  },
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
    me: mocks.auth.me,
    loading: mocks.auth.loading,
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
    vi.clearAllMocks();
    mocks.auth.me = { is_client: false, is_monoblock: false };
    mocks.auth.loading = false;
  });

  afterEach(() => {
    vi.useRealTimers();
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

  it("does not redirect an unresolved saved session after a transient initial failure", () => {
    mocks.auth.me = null;
    localStorage.setItem(authTokenStorageKey("refresh"), "saved-refresh");

    render(<AppShell title="Dashboard">content</AppShell>);

    expect(mocks.loadMe).toHaveBeenCalledTimes(1);
    expect(mocks.replace).not.toHaveBeenCalled();

    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(mocks.loadMe).toHaveBeenCalledTimes(2);
  });

  it("automatically retries a saved session while the tab remains visible", () => {
    vi.useFakeTimers();
    mocks.auth.me = null;
    localStorage.setItem(authTokenStorageKey("refresh"), "saved-refresh");

    render(<AppShell title="Dashboard">content</AppShell>);
    expect(mocks.loadMe).toHaveBeenCalledTimes(1);

    act(() => {
      vi.advanceTimersByTime(2_000);
    });
    expect(mocks.loadMe).toHaveBeenCalledTimes(2);
  });

  it("redirects an unauthenticated shell after the session is explicitly cleared", () => {
    mocks.auth.me = null;

    render(<AppShell title="Dashboard">content</AppShell>);

    expect(mocks.replace).toHaveBeenCalledWith("/login");
  });
});
