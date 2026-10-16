import { ProductPhoto } from "@/components/catalog/product-photo";
import { AddToCart } from "@/components/portal/add-to-cart";
import { Card } from "@/components/ui/card";
import type { PortalProduct } from "@/lib/types";
import { formatCurrency } from "@/lib/utils";

/** Карточка товара для заказа: фото, фасовка, цена покупателя и «В корзину». Каталог портала. */
export function ProductCard({ product }: { product: PortalProduct }) {
  return (
    <Card className="flex flex-col overflow-hidden">
      <ProductPhoto
        url={product.photo_url}
        alt={product.label}
        className="aspect-[4/3] w-full"
        iconClassName="size-10"
      />
      <div className="flex flex-1 flex-col gap-3 p-3 sm:p-4">
        <div className="flex-1">
          <div className="text-sm font-medium leading-snug sm:text-[15px]">{product.label}</div>
          <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">{Number(product.weight_kg)} кг / мешок</div>
        </div>
        {product.price ? (
          <div className="text-lg font-semibold tabular-nums">
            {formatCurrency(product.price, product.currency)}
            <span className="ml-1 text-xs font-normal text-[var(--muted-foreground)]">/ мешок</span>
          </div>
        ) : (
          <div className="text-sm font-medium text-[var(--muted-foreground)]">Цена уточняется</div>
        )}
        <AddToCart product={product} />
      </div>
    </Card>
  );
}
