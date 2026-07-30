import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceRecorder } from "./voice-recorder";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function audioStream() {
  const stop = vi.fn();
  const stream = {
    getTracks: () => [{ stop }],
  } as unknown as MediaStream;
  return { stream, stop };
}

let mediaRecorderConstructor = vi.fn<(stream: MediaStream) => void>();

class FakeMediaRecorder {
  readonly stream: MediaStream;
  readonly mimeType = "audio/webm";
  state: RecordingState = "inactive";
  ondataavailable: ((event: BlobEvent) => void) | null = null;
  onstop: (() => void) | null = null;

  constructor(stream: MediaStream) {
    this.stream = stream;
    mediaRecorderConstructor(stream);
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.onstop?.();
  }
}

const originalMediaDevices = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");
const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

function installGetUserMedia(getUserMedia: ReturnType<typeof vi.fn>) {
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
}

beforeEach(() => {
  mediaRecorderConstructor = vi.fn();
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
});

afterEach(() => {
  vi.unstubAllGlobals();
  if (originalMediaDevices) Object.defineProperty(navigator, "mediaDevices", originalMediaDevices);
  else Reflect.deleteProperty(navigator, "mediaDevices");
  if (originalCreateObjectUrl) Object.defineProperty(URL, "createObjectURL", originalCreateObjectUrl);
  else Reflect.deleteProperty(URL, "createObjectURL");
  if (originalRevokeObjectUrl) Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectUrl);
  else Reflect.deleteProperty(URL, "revokeObjectURL");
});

describe("VoiceRecorder", () => {
  it("locks the start action while microphone permission is pending", async () => {
    const permission = deferred<MediaStream>();
    const getUserMedia = vi.fn().mockReturnValue(permission.promise);
    const { stream, stop } = audioStream();
    installGetUserMedia(getUserMedia);

    const view = render(<VoiceRecorder onChange={vi.fn()} />);
    const start = screen.getByRole("button", { name: "Записать голос" });

    fireEvent.click(start);
    fireEvent.click(start);

    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Подключение…" })).toBeDisabled();

    await act(async () => {
      permission.resolve(stream);
      await permission.promise;
    });

    expect(mediaRecorderConstructor).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Остановить" })).toBeEnabled();

    view.unmount();
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it("stops a stream that resolves after unmount without creating a recorder", async () => {
    const permission = deferred<MediaStream>();
    const getUserMedia = vi.fn().mockReturnValue(permission.promise);
    const { stream, stop } = audioStream();
    installGetUserMedia(getUserMedia);

    const view = render(<VoiceRecorder onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Записать голос" }));
    view.unmount();

    await act(async () => {
      permission.resolve(stream);
      await permission.promise;
    });

    expect(stop).toHaveBeenCalledTimes(1);
    expect(mediaRecorderConstructor).not.toHaveBeenCalled();
  });

  it("releases the microphone when MediaRecorder cannot start", async () => {
    const { stream, stop } = audioStream();
    installGetUserMedia(vi.fn().mockResolvedValue(stream));
    vi.stubGlobal(
      "MediaRecorder",
      class {
        constructor() {
          throw new Error("unsupported codec");
        }
      },
    );

    render(<VoiceRecorder onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Записать голос" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось начать запись звука");
    expect(stop).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Записать голос" })).toBeEnabled();
  });

  it("emits a finished recording and revokes its preview when discarded", async () => {
    const { stream, stop } = audioStream();
    const onChange = vi.fn();
    const createObjectURL = vi.fn().mockReturnValue("blob:voice-preview");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    installGetUserMedia(vi.fn().mockResolvedValue(stream));

    render(<VoiceRecorder onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Записать голос" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Остановить" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Остановить" }));

    expect(await screen.findByLabelText("Записанное голосовое сообщение")).toHaveAttribute("src", "blob:voice-preview");
    expect(onChange).toHaveBeenCalledWith(expect.any(File));
    expect(stop).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:voice-preview");
    expect(onChange).toHaveBeenLastCalledWith(null);
  });
});
