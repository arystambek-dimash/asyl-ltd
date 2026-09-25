import * as React from "react";
import { Search, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type SearchInputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, "size"> & {
  /** Классы обёртки: ширина и место поля в ряду фильтров. */
  wrapperClassName?: string;
  /** Крупное поле под палец — экран грузчика. */
  size?: "default" | "lg";
  /** Кнопка «Очистить поиск» справа, пока в поле что-то есть. */
  onClear?: () => void;
};

/** Поле поиска с иконкой лупы; клик по иконке ставит фокус в поле. */
export function SearchInput({
  wrapperClassName,
  size = "default",
  onClear,
  className,
  value,
  ...props
}: SearchInputProps) {
  const large = size === "lg";
  const clearable = Boolean(onClear) && value !== undefined && value !== "";
  return (
    <div className={cn("relative", wrapperClassName)}>
      <Search
        className={cn(
          "pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted-foreground)]",
          large ? "size-5" : "size-4",
        )}
      />
      <Input
        value={value}
        className={cn(large ? "h-12 pl-10 text-base" : "pl-9", clearable && "pr-9", className)}
        {...props}
      />
      {clearable && (
        <button
          type="button"
          onClick={onClear}
          className="absolute right-2 top-1/2 inline-flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-[var(--muted-foreground)] outline-none hover:bg-[var(--accent)] hover:text-[var(--foreground)] focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/50"
          aria-label="Очистить поиск"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  );
}
