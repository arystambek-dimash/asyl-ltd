import { api } from "@/lib/api";
import type { Product } from "@/lib/types";

// Фото с телефона весит 3–8 МБ, а nginx режет запросы к /api на 5 МБ: ужимаем в браузере,
// сервер всё равно приведёт его к JPEG 1200 px.
const MAX_SIDE_PX = 1600;
const KEEP_ORIGINAL_BYTES = 1.5 * 1024 * 1024;

export const PRODUCT_PHOTO_ACCEPT = "image/jpeg,image/png,image/webp";

export async function downscaleImage(file: File): Promise<Blob> {
  if (typeof createImageBitmap !== "function") return file;
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, MAX_SIDE_PX / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && file.size <= KEEP_ORIGINAL_BYTES) {
      bitmap.close();
      return file;
    }
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const context = canvas.getContext("2d");
    if (!context) return file;
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    return await new Promise<Blob>((resolve) => canvas.toBlob((blob) => resolve(blob ?? file), "image/jpeg", 0.85));
  } catch {
    // Формат, который браузер не открыл (например HEIC), — пусть сервер скажет, что с ним не так.
    return file;
  }
}

/** Изменение фото после сохранения товара: новый файл, удаление или ничего. */
export async function saveProductPhoto(productId: number, change: { file: File | null; removed: boolean }) {
  if (change.file) {
    const form = new FormData();
    form.append("photo", await downscaleImage(change.file), "photo.jpg");
    return (await api.post<Product>(`/products/${productId}/photo/`, form)).data;
  }
  if (change.removed) return (await api.delete<Product>(`/products/${productId}/photo/`)).data;
  return null;
}
