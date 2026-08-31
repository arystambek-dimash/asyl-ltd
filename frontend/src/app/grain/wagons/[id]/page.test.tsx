import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Suspense, type ComponentProps, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { GrainWagon, PassageWeightCapture } from "@/lib/types";
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
  LiveScaleStatus: ({ active, scaleKey, label }: { active: boolean; scaleKey: "truck"; label: string }) =>
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

function processingCapture(overrides: Partial<PassageWeightCapture> = {}): PassageWeightCapture {
  return {
    request_id: "7ea3b52c-f6bc-4ac9-b716-a5e37e2ec1ba",
    action: "entry",
    status: "processing",
    stage: "recognizing",
    camera: "cam1",
    camera_source: "main",
    stable_weight_at: "2026-08-30T10:21:14.381Z",
    weight_kg: 12_000,
    vehicle_number: "",
    recognized_at: null,
    confirmation_votes: null,
    detector_confidence: null,
    ocr_confidence: null,
    response_status: 503,
    retryable: true,
    error_code: "vehicle_recognition_unavailable",
    error_detail: "Связь с камерой прервалась.",
    started_at: "2026-08-30T10:21:14Z",
    updated_at: "2026-08-30T10:21:15Z",
    completed_at: null,
    ...overrides,
  };
}

describe("StageAction automatic scale capture", () => {
  beforeEach(() => {
    sessionStorage.clear();
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
  ])("sends an empty POST for %s", async (_name, value, buttonName, endpoint) => {
    const user = userEvent.setup();
    await renderStage(value as GrainWagon);

    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(screen.queryByText(/Причина ручного ввода/)).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: buttonName as RegExp }));

    expect(postMock).toHaveBeenCalledWith(
      endpoint,
      {},
      {
        headers: {
          "Idempotency-Key": expect.stringMatching(
            /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
          ),
        },
      },
    );
    await waitFor(() => {
      expect(wagonReloadMock).toHaveBeenCalledOnce();
      expect(timelineReloadMock).toHaveBeenCalledOnce();
    });
  });

  it.each([
    ["simple entry", wagon({ direction: "intake" })],
    ["simple exit", wagon({ direction: "intake", status: "at_silo", status_label: "На разгрузке" })],
    ["legacy gross", wagon({ workflow: "legacy", direction: "intake" })],
    ["legacy tare", wagon({ workflow: "legacy", direction: "intake", status: "unloading_completed" })],
  ])("keeps %s intake weighing disabled until wagon scales exist", async (_name, value) => {
    await renderStage(value as GrainWagon);

    expect(screen.getByText("Вагонные весы пока не подключены")).toBeInTheDocument();
    expect(screen.getByText(/Весы машин вывоза здесь не используются/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Получить.*вес|Получить вес/ })).not.toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
  });

  it("shows the truck scale only for an export trip", async () => {
    await renderStage(wagon({ direction: "passage" }));

    expect(screen.getByLabelText("Весы Вывоз")).toHaveAttribute("data-scale-key", "truck");
    expect(screen.queryAllByLabelText(/^Весы /)).toHaveLength(1);
  });

  it("does not show a scale widget for intake wagons", async () => {
    await renderStage(wagon({ direction: "intake" }));

    expect(screen.queryByLabelText(/^Весы /)).not.toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: "Фиксирую показание весов…" })).toBeDisabled();

    rejectRequest(new Error("scale offline"));
    expect(await screen.findByText("Весовой аппарат недоступен")).toBeInTheDocument();
    expect(wagonReloadMock).toHaveBeenCalledOnce();
    expect(timelineReloadMock).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Проверить результат повторно" })).toBeEnabled();
    expect(screen.getByText(/с тем же идентификатором операции/)).toBeInTheDocument();
  });

  it("reuses the same idempotency key after a lost response", async () => {
    postMock.mockRejectedValueOnce(new Error("connection reset")).mockResolvedValueOnce({ data: {} });
    const user = userEvent.setup();
    await renderStage(wagon());

    await user.click(await screen.findByRole("button", { name: /Получить вес пустой/ }));
    await screen.findByRole("button", { name: "Проверить результат повторно" });
    const firstKey = postMock.mock.calls[0][2].headers["Idempotency-Key"];

    await user.click(screen.getByRole("button", { name: "Проверить результат повторно" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(2));
    const secondKey = postMock.mock.calls[1][2].headers["Idempotency-Key"];

    expect(secondKey).toBe(firstKey);
    expect(sessionStorage.length).toBe(0);
  });

  it("recovers the processing request id from the server after session storage was lost", async () => {
    const requestId = "d38deba1-5ee8-47ee-8308-332096b76ccc";
    const user = userEvent.setup();
    await renderStage(
      wagon({
        vehicle_recognition_captures: [processingCapture({ request_id: requestId })],
      }),
    );

    const retry = await screen.findByRole("button", { name: "Проверить результат повторно" });
    expect(sessionStorage.length).toBe(1);
    await user.click(retry);

    expect(postMock).toHaveBeenCalledWith(
      "/grain/wagons/7/entry-weight/",
      {},
      { headers: { "Idempotency-Key": requestId } },
    );
  });

  it("hydrates an uncertain capture from session storage after a remount", async () => {
    const requestId = "5d066ef4-fe98-40ea-97ae-fbd67a758189";
    sessionStorage.setItem("asyl:passage-weight-capture:v1:1:7:entry-weight", requestId);
    const user = userEvent.setup();
    await renderStage(wagon());

    await user.click(await screen.findByRole("button", { name: "Проверить результат повторно" }));

    expect(postMock).toHaveBeenCalledWith(
      "/grain/wagons/7/entry-weight/",
      {},
      { headers: { "Idempotency-Key": requestId } },
    );
  });

  it("clears a stored request after the server reports that capture as terminal", async () => {
    const requestId = "1dbb1f4f-ea16-4867-b9ef-449cf6f460f5";
    sessionStorage.setItem("asyl:passage-weight-capture:v1:1:7:entry-weight", requestId);
    await renderStage(
      wagon({
        vehicle_recognition_captures: [
          processingCapture({
            request_id: requestId,
            status: "failed",
            stage: "done",
            retryable: false,
            completed_at: "2026-08-30T10:21:16Z",
          }),
        ],
      }),
    );

    expect(await screen.findByRole("button", { name: /Получить вес пустой/ })).toBeEnabled();
    expect(sessionStorage.length).toBe(0);
  });

  it("adopts the server request id from a resumable conflict", async () => {
    const serverRequestId = "a762d132-e03d-4cc4-984d-9792a9d4079c";
    postMock
      .mockRejectedValueOnce({
        response: {
          status: 409,
          data: {
            code: "passage_capture_resume_required",
            request_id: serverRequestId,
            retryable: true,
          },
        },
      })
      .mockResolvedValueOnce({ data: {} });
    const user = userEvent.setup();
    await renderStage(wagon());

    await user.click(await screen.findByRole("button", { name: /Получить вес пустой/ }));
    await user.click(await screen.findByRole("button", { name: "Проверить результат повторно" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(2));

    expect(postMock.mock.calls[1][2].headers["Idempotency-Key"]).toBe(serverRequestId);
  });

  it("retains the same idempotency key for a proxy 502 without a retryability body", async () => {
    postMock.mockRejectedValueOnce({ response: { status: 502, data: {} } }).mockResolvedValueOnce({ data: {} });
    const user = userEvent.setup();
    await renderStage(wagon());

    await user.click(await screen.findByRole("button", { name: /Получить вес пустой/ }));
    const firstKey = postMock.mock.calls[0][2].headers["Idempotency-Key"];
    await user.click(await screen.findByRole("button", { name: "Проверить результат повторно" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(2));

    expect(postMock.mock.calls[1][2].headers["Idempotency-Key"]).toBe(firstKey);
  });

  it("honors an explicit terminal response even when its HTTP status is 503", async () => {
    postMock
      .mockRejectedValueOnce({ response: { status: 503, data: { retryable: false } } })
      .mockResolvedValueOnce({ data: {} });
    const user = userEvent.setup();
    await renderStage(wagon());

    await user.click(await screen.findByRole("button", { name: /Получить вес пустой/ }));
    const firstKey = postMock.mock.calls[0][2].headers["Idempotency-Key"];
    await user.click(await screen.findByRole("button", { name: /Получить вес пустой/ }));
    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(2));

    expect(postMock.mock.calls[1][2].headers["Idempotency-Key"]).not.toBe(firstKey);
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
