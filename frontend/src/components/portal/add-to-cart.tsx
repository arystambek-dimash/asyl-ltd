"use client";
import { ShoppingCart } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { PortalProduct } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useCart } from "@/store/cart";
import { QuantityStepper } from "./quantity-stepper";

/** Кнопка «В корзину», после первого нажатия — счётчик количества. */
export function AddToCart({ product, className }: { product: PortalProduct; className?: string }) {
  const cart = useCart();
  const quantity = cart.quantityOf(product.id);
  if (quantity === 0) {
    return (
      <Button
        className={cn("w-full", className)}
        aria-label={`Добавить в корзину: ${product.label}`}
        onClick={() => cart.add(product.id)}
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
      className={cn("w-full", className)}
    />
  );
}
