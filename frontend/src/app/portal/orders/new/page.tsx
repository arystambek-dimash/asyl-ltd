import { redirect } from "next/navigation";

// Заказ теперь оформляется из корзины; старый адрес остаётся для закладок и ссылок.
export default function PortalNewOrderPage() {
  redirect("/portal/cart");
}
