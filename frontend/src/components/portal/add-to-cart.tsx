"use client";
import { useState } from "react";
import { ShoppingCart } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { PortalProduct } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useCart } from "@/store/cart";
import { QuantityStepper } from "./quantity-stepper";

/** Кнопка «В корзину», после первого нажатия — счётчик с полем для числа мешков. */
export function AddToCart({ product, className }: { product: PortalProduct; className?: string }) {
  const cart = useCart();
  // Только что добавили — курсор сразу в число: мешки заказывают десятками и сотнями.
  const [justAdded, setJustAdded] = useState(false);
  const quantity = cart.quantityOf(product.id);
  if (quantity === 0) {
    return (
      <Button
        className={cn("h-11 w-full", className)}
        aria-label={`Добавить в корзину: ${product.label}`}
        onClick={() => {
          setJustAdded(true);
          cart.add(product.id);
        }}
      >
        <ShoppingCart className="size-4" />
        <span className="sm:hidden">В корзину</span>
        <span className="hidden sm:inline">Добавить в корзину</span>
      </Button>
    );
  }
  return (
    <QuantityStepper
      value={quantity}
      onChange={(next) => cart.setQuantity(product.id, next)}
      label={product.label}
      autoFocus={justAdded}
      className={cn("w-full", className)}
    />
  );
}
