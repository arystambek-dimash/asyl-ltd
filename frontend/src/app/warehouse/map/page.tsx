import { redirect } from "next/navigation";

// Схема территории удалена; старые ссылки ведут на склад.
export default function LegacyFactoryMapPage() {
  redirect("/warehouse");
}
