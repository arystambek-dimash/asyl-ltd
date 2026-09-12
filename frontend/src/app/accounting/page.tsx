"use client";
import { Suspense } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { CashierDesktop } from "@/components/cashier/desktop";
import { MobileCashier } from "@/components/cashier/mobile/mobile-cashier";
import { useCashier } from "@/components/cashier/use-cashier";
import { CASHIER_ENTRY_PERMS, cashierPerms, resolveView, type CashView } from "@/components/cashier/view";
import { RequirePerm } from "@/components/require-perm";
import { useIsMobile } from "@/lib/use-media-query";
import { useAuth } from "@/store/auth";

function CashierInner() {
  const { me } = useAuth();
  const perms = cashierPerms(me);
  const mobile = useIsMobile();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  // Экран живёт в URL: диплинки открывают нужную вкладку.
  const view = resolveView(searchParams.get("view"), perms, mobile);
  const model = useCashier({ view, mobile, perms, me });

  function selectTab(next: CashView) {
    router.replace(`${pathname}?view=${next}`, { scroll: false });
  }

  if (mobile) return <MobileCashier model={model} />;
  return <CashierDesktop model={model} onTab={selectTab} />;
}

export default function CashierPage() {
  // Доступ, если есть хотя бы одна из секций: очередь, аналитика с долгами или транзакции.
  return (
    <RequirePerm perm={CASHIER_ENTRY_PERMS} title="Касса">
      <Suspense fallback={null}>
        <CashierInner />
      </Suspense>
    </RequirePerm>
  );
}
