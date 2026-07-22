import { describe, expect, it } from "vitest";
import { resolveApiMediaUrl, resolveSafeImageUrl } from "@/lib/safe-url";

describe("resolveApiMediaUrl", () => {
  const apiBaseUrl = "https://api.example.com/api";
  const pageOrigin = "https://app.example.com";

  it.each([
    ["/media/camera.jpg", "https://api.example.com/media/camera.jpg"],
    ["https://api.example.com/media/camera.jpg", "https://api.example.com/media/camera.jpg"],
  ])("allows API-origin media URL %s", (value, expected) => {
    expect(resolveApiMediaUrl(value, apiBaseUrl, pageOrigin)).toBe(expected);
  });

  it("preserves a trailing API base path for relative media", () => {
    expect(resolveApiMediaUrl("recording.mp4", `${apiBaseUrl}/`, pageOrigin)).toBe(
      "https://api.example.com/api/recording.mp4",
    );
  });

  it.each([
    "https://attacker.example/camera.jpg",
    "javascript:alert(1)",
    "data:image/png;base64,AAAA",
    "ftp://api.example.com/camera.jpg",
  ])("rejects unsafe media URL %s", (value) => {
    expect(resolveApiMediaUrl(value, apiBaseUrl, pageOrigin)).toBe("");
  });
});

describe("resolveSafeImageUrl", () => {
  it.each([
    ["https://cdn.example.com/image.webp", "https://cdn.example.com/image.webp"],
    ["/images/logo.png", "https://app.example.com/images/logo.png"],
    ["data:image/png;base64,AAAA", "data:image/png;base64,AAAA"],
    ["http://camera.local/frame.jpg", "http://camera.local/frame.jpg", "http://app.local"],
  ])("allows safe image URL %s", (value, expected, origin = "https://app.example.com") => {
    expect(resolveSafeImageUrl(value, origin)).toBe(expected);
  });

  it.each([
    "javascript:alert(1)",
    "data:image/svg+xml;base64,PHN2Zz4=",
    "data:text/html;base64,PGgxPkJvb208L2gxPg==",
    "http://camera.local/frame.jpg",
    "",
  ])("rejects unsafe image URL %s", (value) => {
    expect(resolveSafeImageUrl(value, "https://app.example.com")).toBe("");
  });
});
