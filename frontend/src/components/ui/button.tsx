import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex cursor-pointer items-center justify-center gap-2 whitespace-nowrap rounded-xl text-sm font-semibold transition-all duration-200 outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/28 disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-[var(--primary)] text-[var(--primary-foreground)] shadow-[0_10px_24px_-16px_rgba(23,36,29,.8)] hover:-translate-y-0.5 hover:brightness-110 active:translate-y-0",
        destructive:
          "bg-[var(--destructive)] text-[var(--destructive-foreground)] shadow-sm hover:-translate-y-0.5 hover:brightness-105 active:translate-y-0",
        outline:
          "border border-[var(--input)] bg-[var(--card)] text-[var(--foreground)] shadow-[0_4px_14px_-12px_rgba(23,32,27,.6)] hover:-translate-y-0.5 hover:border-[var(--muted-foreground)]/40 hover:bg-[var(--accent)] active:translate-y-0",
        secondary:
          "bg-[var(--secondary)] text-[var(--secondary-foreground)] hover:-translate-y-0.5 hover:bg-[var(--accent)] active:translate-y-0",
        ghost: "text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--accent-foreground)]",
        link: "text-[var(--ring)] underline-offset-4 hover:underline",
      },
      size: {
        default: "h-11 px-4 py-2",
        sm: "h-11 rounded-xl px-3 text-xs sm:h-9 sm:rounded-lg",
        lg: "h-12 rounded-xl px-6",
        icon: "size-11 sm:size-10",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props} />
  ),
);
Button.displayName = "Button";
export { buttonVariants };
