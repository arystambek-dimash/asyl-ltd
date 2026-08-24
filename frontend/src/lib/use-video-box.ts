"use client";

import { useEffect, useState } from "react";

export interface VideoBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Measure the pixels occupied by a centered video inside an overlay's parent.
 * The video and overlay are siblings, so both bbox and line layers share the
 * exact same coordinate system even when object-contain adds letterboxing.
 */
export function useVideoBox(container: HTMLElement | null): VideoBox | null {
  const [box, setBox] = useState<VideoBox | null>(null);

  useEffect(() => {
    if (!container) return;
    const parent = container.parentElement;
    if (!parent) return;

    let video: HTMLVideoElement | null = null;
    const clearBox = () => setBox((current) => (current === null ? current : null));
    const commitBox = (next: VideoBox) =>
      setBox((current) =>
        current &&
        current.left === next.left &&
        current.top === next.top &&
        current.width === next.width &&
        current.height === next.height
          ? current
          : next,
      );
    const measure = () => {
      if (!video) return clearBox();
      const { videoWidth, videoHeight } = video;
      const { clientWidth, clientHeight } = parent;
      if (!videoWidth || !videoHeight || !clientWidth || !clientHeight) return clearBox();

      const objectFit = window.getComputedStyle(video).objectFit;
      const scale =
        objectFit === "cover"
          ? Math.max(clientWidth / videoWidth, clientHeight / videoHeight)
          : Math.min(clientWidth / videoWidth, clientHeight / videoHeight);
      const width = videoWidth * scale;
      const height = videoHeight * scale;
      commitBox({
        left: (clientWidth - width) / 2,
        top: (clientHeight - height) / 2,
        width,
        height,
      });
    };

    const bindVideo = () => {
      const next = parent.querySelector("video");
      if (next === video) {
        measure();
        return;
      }
      video?.removeEventListener("loadedmetadata", measure);
      video?.removeEventListener("resize", measure);
      video = next;
      video?.addEventListener("loadedmetadata", measure);
      video?.addEventListener("resize", measure);
      measure();
    };

    bindVideo();
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    resizeObserver?.observe(parent);
    // CameraStream can appear after its auth token is ready. Rebind instead of
    // leaving an editor that measured before the <video> existed.
    const mutationObserver = new MutationObserver(bindVideo);
    mutationObserver.observe(parent, { childList: true, subtree: true });

    return () => {
      video?.removeEventListener("loadedmetadata", measure);
      video?.removeEventListener("resize", measure);
      resizeObserver?.disconnect();
      mutationObserver.disconnect();
    };
  }, [container]);

  return box;
}
