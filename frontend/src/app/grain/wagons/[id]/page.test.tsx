import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Suspense, type ComponentProps, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { GrainWagon } from "@/lib/types";
import GrainWagonPage from "./page";

const postMock = vi.hoisted(() => vi.fn());
const deleteMock = vi.hoisted(() => vi.fn());
const replaceMock = vi.hoisted(() => vi.fn());
const authState = vi.hoisted(() => ({ permissions: ["grain.weigh"] as string[] }));
const useApiMock = vi.hoisted(() => vi.fn());
const wagonReloadMock = vi.hoisted(() => vi.fn());
const timelineReloadMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { post: postMock, delete: deleteMock },
  apiError: () => "Весовой аппарат недоступен",
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));
vi.mock("@/lib/use-api", () => ({
  useApi: useApiMock,
}));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({
    me: {
      id: 1,
      username: "scale-operator",
      permissions: authState.permissions,
      is_superuser: false,
    },
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
vi.mock("@/components/grain/live-scale-status", () => ({
  LiveScaleStatus: ({ active, scaleKey, label }: { active: boolean; scaleKey: "wagon" | "truck"; label: string }) =>
    active ? <div aria-label={`Весы ${label}`} data-scale-key={scaleKey} /> : null,
}));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentProps<"a">) => <a {...props}>{children}</a>,
}));

let activeWagon: GrainWagon;

function wagon(overrides: Partial<GrainWagon> = {}): GrainWagon {
  return {
    id: 7,
    supply: null,
    number: "123 ABC",
    number_source: "manual",
    workflow: "simple",
    direction: "passage",
    cargo_name: "Отруби",
    status: "arrived",
    status_label: "Прибыл",
    unplanned: false,
    supplier: "",
    culture: "",
    grain_class: "",
    grain_type: null,
    grain_type_name: "",
    document_weight_kg: null,
    expected_weight_kg: null,
    arrived_at: null,
    gross_weight_kg: null,
    tare_weight_kg: null,
    net_weight_kg: null,
    entry_weight_kg: null,
    exit_weight_kg: null,
    weight_difference_kg: null,
    weight_difference_percent: null,
    weight_matches: null,
    assigned_silo: null,
    assigned_silo_name: null,
    silo_arrived_at: null,
    exited_at: null,
    created_at: "2026-08-12T00:00:00Z",
    ...overrides,
  };
}

describe("StageAction automatic scale capture", () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({ data: {} });
    deleteMock.mockReset();
    deleteMock.mockResolvedValue({ data: { reverted_kg: 0 } });
    replaceMock.mockReset();
    authState.permissions = ["grain.weigh"];
    wagonReloadMock.mockReset();
    timelineReloadMock.mockReset();
    activeWagon = wagon();
    useApiMock.mockReset();
    useApiMock.mockImplementation((url: string | null) => {
      if (url === "/grain/wagons/7/") {
        return { data: activeWagon, loading: false, error: "", reload: wagonReloadMock };
      }
      if (url === "/grain/wagons/7/timeline/") {
        return { data: [], loading: false, error: "", reload: timelineReloadMock };
      }
      return { data: null, loading: false, error: "", reload: vi.fn() };
    });
  });

  async function renderStage(value: GrainWagon) {
    activeWagon = value;
    const params = Promise.resolve({ id: "7" });
    await act(async () => {
      render(
        <Suspense fallback={<p>Загрузка…</p>}>
          <GrainWagonPage params={params} />
        </Suspense>,
      );
      await params;
    });
  }

  it.each([
    ["simple entry", wagon(), /Получить вес пустой/, "/grain/wagons/7/entry-weight/"],
    [
      "simple exit",
      wagon({ status: "at_silo", status_label: "На погрузке" }),
      /Получить вес гружёной/,
      "/grain/wagons/7/exit-weight/",
    ],
    [
      "legacy gross",
      wagon({ workflow: "legacy", direction: "intake" }),
      /Получить вес брутто/,
      "/grain/wagons/7/gross/",
    ],
    [
      "legacy tare",
      wagon({ workflow: "legacy", direction: "intake", status: "unloading_completed" }),
      /Получить вес тары/,
      "/grain/wagons/7/tare/",
    ],
  ])("sends an empty POST for %s", async (_name, value, buttonName, endpoint) => {
    const user = userEvent.setup();
    await renderStage(value as GrainWagon);

    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(screen.queryByText(/Причина ручного ввода/)).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: buttonName as RegExp }));

    expect(postMock).toHaveBeenCalledWith(endpoint, {});
    await waitFor(() => {
      expect(wagonReloadMock).toHaveBeenCalledOnce();
      expect(timelineReloadMock).toHaveBeenCalledOnce();
    });
  });

  it.each([
    ["passage", "Вывоз", "truck"],
    ["intake", "Вагоны", "wagon"],
  ] as const)("shows only the %s scale in the page actions", async (direction, label, scaleKey) => {
    await renderStage(wagon({ direction }));

    expect(screen.getByLabelText(`Весы ${label}`)).toHaveAttribute("data-scale-key", scaleKey);
    expect(screen.queryAllByLabelText(/^Весы /)).toHaveLength(1);
  });

  it("shows loading and the backend error without losing the action", async () => {
    let rejectRequest!: (error: Error) => void;
    postMock.mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectRequest = reject;
      }),
    );
    const user = userEvent.setup();
    await renderStage(wagon());

    await user.click(await screen.findByRole("button", { name: /Получить вес пустой/ }));
    expect(screen.getByRole("button", { name: "Получаю вес с весов…" })).toBeDisabled();

    rejectRequest(new Error("scale offline"));
    expect(await screen.findByText("Весовой аппарат недоступен")).toBeInTheDocument();
    expect(wagonReloadMock).toHaveBeenCalledOnce();
    expect(timelineReloadMock).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: /Получить вес пустой/ })).toBeEnabled();
  });
});

describe("Grain wagon deletion", () => {
  beforeEach(() => {
    postMock.mockReset();
    deleteMock.mockReset();
    deleteMock.mockResolvedValue({ data: { reverted_kg: 0 } });
    replaceMock.mockReset();
    wagonReloadMock.mockReset();
    timelineReloadMock.mockReset();
    useApiMock.mockReset();
    useApiMock.mockImplementation((url: string | null) => {
      if (url === "/grain/wagons/7/") {
        return { data: activeWagon, loading: false, error: "", reload: wagonReloadMock };
      }
      if (url === "/grain/wagons/7/timeline/") {
        return { data: [], loading: false, error: "", reload: timelineReloadMock };
      }
      return { data: null, loading: false, error: "", reload: vi.fn() };
    });
  });

  async function renderPage(value: GrainWagon, permissions: string[]) {
    activeWagon = value;
    authState.permissions = permissions;
    const params = Promise.resolve({ id: "7" });
    await act(async () => {
      render(
        <Suspense fallback={<p>Загрузка…</p>}>
          <GrainWagonPage params={params} />
        </Suspense>,
      );
      await params;
    });
  }

  it("hides the destructive action without grain.delete", async () => {
    await renderPage(wagon({ status: "completed", status_label: "Завершён" }), ["grain.weigh"]);

    expect(screen.queryByRole("button", { name: "Удалить рейс" })).not.toBeInTheDocument();
  });

  it.each(["expected", "unplanned"])("hides delete for the backend-unsupported %s status", async (status) => {
    await renderPage(wagon({ status, status_label: status }), ["grain.view", "grain.delete"]);

    expect(screen.queryByRole("button", { name: "Удалить рейс" })).not.toBeInTheDocument();
  });

  it("deletes with a required reason and redirects to the grain list", async () => {
    const user = userEvent.setup();
    await renderPage(wagon({ status: "at_silo", status_label: "У силоса" }), ["grain.view", "grain.delete"]);

    await user.click(screen.getByRole("button", { name: "Удалить рейс" }));
    expect(screen.getByRole("button", { name: "Удалить активный рейс" })).toBeDisabled();
    await user.type(screen.getByLabelText("Причина удаления *"), "Тестовый заезд");
    await user.click(screen.getByRole("button", { name: "Удалить активный рейс" }));

    expect(deleteMock).toHaveBeenCalledWith("/grain/wagons/7/delete/", {
      data: { reason: "Тестовый заезд" },
    });
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/grain"));
  });
});
