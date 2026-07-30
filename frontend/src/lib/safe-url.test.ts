import { describe, expect, it } from "vitest";
import { resolveApiMediaUrl } from "@/lib/safe-url";

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
