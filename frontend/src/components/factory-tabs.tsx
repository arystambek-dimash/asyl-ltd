"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Boxes, Factory, Warehouse } from "lucide-react";
import { can } from "@/lib/can";
import { cn } from "@/lib/utils";
import { useAuth } from "@/store/auth";

export function FactoryTabs() {
  const { me } = useAuth();
  const pathname = usePathname();
  const tabs = [
    ...(can(me, "warehouse.view")
      ? [{ href: "/warehouse", label: "Склад", icon: Boxes, active: pathname === "/warehouse" }]
      : []),
    ...(can(me, "grain.view")
      ? [
          {
            href: "/warehouse/silos",
            label: "Силосы",
            icon: Warehouse,
            active: pathname.startsWith("/warehouse/silos"),
          },
        ]
      : []),
  ];

  return (
    <div className="flex h-12 items-center gap-1 sm:h-full" aria-label="Участки завода">
      <span className="mr-1 hidden items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)] xl:flex">
        <Factory className="size-3.5" /> Участки
      </span>
      {tabs.map(({ href, label, icon: Icon, active }) => (
        <Link
          key={href}
          href={href}
          aria-current={active ? "page" : undefined}
          className={cn(
            "relative inline-flex h-full items-center gap-2 whitespace-nowrap border-b-2 px-3 text-sm transition-colors",
            active
              ? "border-[#a66a20] font-semibold text-[var(--foreground)]"
              : "border-transparent text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
          )}
        >
          <Icon className="size-4" /> {label}
        </Link>
      ))}
    </div>
  );
}
