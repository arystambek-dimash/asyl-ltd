"use client";

import { useEffect, useState } from "react";

export interface VideoBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** A still frame (``<img data-video-box-source>``) can stand in for live video. */
const STILL_FRAME_SELECTOR = "img[data-video-box-source]";
// ``load`` refits the layer whenever a refreshed still frame arrives.
const MEDIA_EVENTS = ["loadedmetadata", "resize", "load"] as const;

function mediaSize(media: HTMLVideoElement | HTMLImageElement): [number, number] {
  return media instanceof HTMLVideoElement
    ? [media.videoWidth, media.videoHeight]
    : [media.naturalWidth, media.naturalHeight];
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

    let video: HTMLVideoElement | HTMLImageElement | null = null;
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
      const [videoWidth, videoHeight] = mediaSize(video);
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

    const unbind = () => {
      for (const event of MEDIA_EVENTS) video?.removeEventListener(event, measure);
    };

    const bindVideo = () => {
      const next = parent.querySelector("video") ?? parent.querySelector<HTMLImageElement>(STILL_FRAME_SELECTOR);
      if (next === video) {
        measure();
        return;
      }
      unbind();
      video = next;
      for (const event of MEDIA_EVENTS) video?.addEventListener(event, measure);
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
      unbind();
      resizeObserver?.disconnect();
      mutationObserver.disconnect();
    };
  }, [container]);

  return box;
}
