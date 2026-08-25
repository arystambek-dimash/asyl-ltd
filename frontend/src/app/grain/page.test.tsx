import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GrainPage from "./page";

const postMock = vi.hoisted(() => vi.fn());
const pushMock = vi.hoisted(() => vi.fn());
const reloadMock = vi.hoisted(() => vi.fn());
const pagedApiMock = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));
vi.mock("@/lib/api", () => ({
  api: { post: postMock },
  apiError: () => "Не удалось оформить вывоз",
}));
vi.mock("@/lib/can", () => ({
  can: () => true,
}));
vi.mock("@/lib/use-api", () => ({
  useApi: () => ({ data: [], loading: false, error: "", reload: reloadMock }),
}));
vi.mock("@/lib/use-paged-api", () => ({
  usePagedApi: (url: string | null, pageSize: number) => pagedApiMock(url, pageSize),
}));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({
    me: { id: 1, username: "operator", permissions: ["grain.arrive", "grain.weigh"] },
  }),
}));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children, actions }: { children: ReactNode; actions?: ReactNode }) => (
    <main>
      {actions}
      {children}
    </main>
  ),
}));
vi.mock("@/components/grain/grain-toolbar", () => ({
  GrainToolbar: ({ onPassage }: { onPassage: () => void }) => (
    <button type="button" onClick={onPassage}>
      Открыть вывоз
    </button>
  ),
}));
vi.mock("@/components/grain/wagon-number-camera", () => ({
  WagonNumberCameraWorkspace: () => null,
}));
vi.mock("@/components/grain/wagon-table", () => ({
  FlowEmptyState: () => null,
  WagonTable: () => null,
}));
vi.mock("@/components/ui/modal", () => ({
  Modal: ({ open, title, children }: { open: boolean; title: string; children: ReactNode }) =>
    open ? <section aria-label={title}>{children}</section> : null,
}));

describe("Grain passage creation", () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({ data: { id: 91, number: "123 ABC" } });
    pushMock.mockReset();
    reloadMock.mockReset();
    pagedApiMock.mockReset();
    pagedApiMock.mockReturnValue({
      items: [],
      count: 0,
      hasMore: false,
      loading: false,
      loadingMore: false,
      error: "",
      reload: reloadMock,
      loadMore: vi.fn(),
    });
  });

  it("loads separate intake and export tables with contextual tabs", async () => {
    const user = userEvent.setup();
    render(<GrainPage />);

    expect(pagedApiMock).toHaveBeenCalledWith("/grain/wagons/?scope=on_site&direction=intake", 50);
    expect(screen.getByRole("tablist", { name: "Направление рейса" })).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Статус рейсов" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Ожидаются" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Камера проходной" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Ожидаются" }));
    expect(screen.getByRole("tab", { name: "Ожидаются" })).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByRole("tab", { name: "Вывоз" }));

    await waitFor(() =>
      expect(pagedApiMock).toHaveBeenCalledWith("/grain/wagons/?scope=on_site&direction=passage", 50),
    );
    expect(screen.queryByRole("tab", { name: "Ожидаются" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Камера проходной" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "На территории" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Завершённые" })).toBeInTheDocument();
  });

  it("opens the newly created passage card for immediate weighing", async () => {
    const user = userEvent.setup();
    render(<GrainPage />);

    await user.click(screen.getByRole("button", { name: "Открыть вывоз" }));
    await user.type(screen.getByLabelText("Номер машины"), "123 ABC");
    await user.click(screen.getByRole("button", { name: /Оформить вывоз/ }));

    expect(postMock).toHaveBeenCalledWith("/grain/wagons/passage/", {
      number: "123 ABC",
      cargo_name: "Отруби",
      note: "",
    });
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/grain/wagons/91"));
  });
});
