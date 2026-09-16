import { beforeEach, expect, it, vi } from "vitest";
import { saveProductPhoto } from "./product-photo";

const api = vi.hoisted(() => ({ post: vi.fn(), delete: vi.fn() }));
vi.mock("@/lib/api", () => ({ api }));

beforeEach(() => {
  api.post.mockReset().mockResolvedValue({ data: { id: 3, photo_url: "/api/product-photos/3/?token=t" } });
  api.delete.mockReset().mockResolvedValue({ data: { id: 3, photo_url: null } });
});

it("загружает выбранное фото отдельным multipart-запросом", async () => {
  const file = new File(["jpeg"], "flour.jpg", { type: "image/jpeg" });

  const saved = await saveProductPhoto(3, { file, removed: false });

  expect(saved?.photo_url).toContain("/api/product-photos/3/");
  const [url, form] = api.post.mock.calls[0];
  expect(url).toBe("/products/3/photo/");
  expect((form as FormData).get("photo")).toBeInstanceOf(Blob);
});

it("удаляет фото или ничего не делает, если его не трогали", async () => {
  expect(await saveProductPhoto(3, { file: null, removed: false })).toBeNull();
  expect(api.post).not.toHaveBeenCalled();

  await saveProductPhoto(3, { file: null, removed: true });
  expect(api.delete).toHaveBeenCalledWith("/products/3/photo/");
});
