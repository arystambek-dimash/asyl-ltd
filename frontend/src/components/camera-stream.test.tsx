import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  ensureCameraStreamToken: vi.fn(),
}));

vi.mock("@/lib/camera-stream-auth", () => ({
  ensureCameraStreamToken: mocks.ensureCameraStreamToken,
}));

import { CameraStream } from "./camera-stream";

class FakeMediaStream {
  private readonly tracks: MediaStreamTrack[] = [];

  addTrack(track: MediaStreamTrack) {
    this.tracks.push(track);
  }

  getTracks() {
    return [...this.tracks];
  }
}

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  readonly send = vi.fn();
  readonly close = vi.fn(() => {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close"));
  });

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }
}

class FakePeerConnection {
  static instances: FakePeerConnection[] = [];

  connectionState: RTCPeerConnectionState = "connecting";
  onicecandidate: ((event: RTCPeerConnectionIceEvent) => void) | null = null;
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  readonly addTransceiver = vi.fn();
  readonly createOffer = vi.fn().mockResolvedValue({ type: "offer", sdp: "test-offer" });
  readonly setLocalDescription = vi.fn().mockResolvedValue(undefined);
  readonly addIceCandidate = vi.fn().mockResolvedValue(undefined);
  readonly setRemoteDescription = vi.fn().mockResolvedValue(undefined);
  readonly getStats = vi.fn().mockResolvedValue(new Map());
  readonly close = vi.fn(() => {
    this.connectionState = "closed";
  });

  constructor() {
    FakePeerConnection.instances.push(this);
  }

  emitTrack(track: MediaStreamTrack) {
    this.ontrack?.({ track } as RTCTrackEvent);
  }

  connect() {
    this.connectionState = "connected";
    this.onconnectionstatechange?.();
  }
}

function track() {
  return {
    id: "video-track",
    stop: vi.fn(),
  } as unknown as MediaStreamTrack;
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  FakePeerConnection.instances = [];
  mocks.ensureCameraStreamToken.mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
  vi.stubGlobal("MediaStream", FakeMediaStream);
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal("RTCPeerConnection", FakePeerConnection);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CameraStream", () => {
  it("becomes visible when media starts before WebRTC reports connected", async () => {
    const onStateChange = vi.fn();
    const view = render(<CameraStream src="cam2ai" onStateChange={onStateChange} />);
    const video = view.container.querySelector("video");
    expect(video).not.toBeNull();

    await waitFor(() => expect(FakePeerConnection.instances).toHaveLength(1));
    const peer = FakePeerConnection.instances[0];
    const videoTrack = track();

    act(() => {
      peer.emitTrack(videoTrack);
      video?.dispatchEvent(new Event("playing"));
    });

    expect(onStateChange).not.toHaveBeenCalledWith(true);
    expect(video).toHaveStyle({ visibility: "hidden" });

    act(() => peer.connect());

    expect(onStateChange).toHaveBeenCalledWith(true);
    expect(video).not.toHaveStyle({ visibility: "hidden" });
    expect(video).toHaveAttribute("data-transport", "webrtc-udp");

    view.unmount();
    expect(videoTrack.stop).toHaveBeenCalledOnce();
    expect(peer.close).toHaveBeenCalledOnce();
    expect(FakeWebSocket.instances[0].close).toHaveBeenCalledOnce();
  });

  it("becomes visible when media starts after WebRTC is connected", async () => {
    const onStateChange = vi.fn();
    const view = render(<CameraStream src="cam2" onStateChange={onStateChange} />);
    const video = view.container.querySelector("video");

    await waitFor(() => expect(FakePeerConnection.instances).toHaveLength(1));
    const peer = FakePeerConnection.instances[0];

    act(() => {
      peer.emitTrack(track());
      peer.connect();
      video?.dispatchEvent(new Event("loadeddata"));
    });

    expect(onStateChange).toHaveBeenCalledWith(true);
    expect(video).not.toHaveStyle({ visibility: "hidden" });
  });
});
