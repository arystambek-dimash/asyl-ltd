"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Boxes, Map, Warehouse } from "lucide-react";
import { can } from "@/lib/can";
import { cn } from "@/lib/utils";
import { useAuth } from "@/store/auth";

export function FactoryTabs() {
  const { me } = useAuth();
  const pathname = usePathname();
  const canSeeFactory = can(me, "warehouse.view") || can(me, "grain.view");
  const tabs = [
    ...(canSeeFactory
      ? [
          {
            href: "/warehouse/map",
            label: "Схема",
            icon: Map,
            active: pathname.startsWith("/warehouse/map"),
          },
        ]
      : []),
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
    <div className="flex h-12 items-center gap-1 sm:h-full" aria-label="Разделы завода">
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
