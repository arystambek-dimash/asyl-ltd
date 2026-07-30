import Image from "next/image";
import { Boxes, Factory, ShieldCheck, Truck } from "lucide-react";

export function AuthShell({
  eyebrow,
  title,
  description,
  children,
  wide = false,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children: React.ReactNode;
  wide?: boolean;
}) {
  const capabilities = [
    { icon: Boxes, label: "Склад", text: "актуальные остатки продукции" },
    { icon: Truck, label: "Погрузка", text: "контроль машин и отгрузок" },
    { icon: ShieldCheck, label: "Учёт", text: "роли, операции и журнал действий" },
  ];

  return (
    <div className="grid min-h-dvh bg-[var(--workspace)] lg:grid-cols-[minmax(360px,0.82fr)_minmax(520px,1.18fr)]">
      <aside className="relative hidden overflow-x-hidden overflow-y-auto bg-[var(--sidebar)] p-8 text-[var(--sidebar-foreground)] lg:flex lg:flex-col xl:p-10">
        <div
          aria-hidden="true"
          className="absolute -right-28 -top-24 size-[430px] rounded-full border border-white/10"
        />
        <div aria-hidden="true" className="absolute -right-8 top-8 size-[250px] rounded-full border border-white/10" />
        <div className="relative flex items-center gap-3">
          <span className="flex size-12 items-center justify-center rounded-2xl bg-white shadow-lg">
            <Image src="/logo-mark.png" alt="" width={34} height={34} className="size-8 object-contain" />
          </span>
          <div>
            <div className="text-base font-extrabold tracking-[0.04em]">ASYL-LTD</div>
            <div className="mt-0.5 text-[10px] font-bold uppercase tracking-[0.16em] text-[var(--sidebar-muted)]">
              Мельничный комплекс
            </div>
          </div>
        </div>

        <div className="relative my-auto max-w-xl py-8">
          <span className="mb-5 flex size-12 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.06] text-[#dceebf]">
            <Factory className="size-7" />
          </span>
          <p className="text-[11px] font-bold uppercase tracking-[0.2em] text-[#dceebf]">Производственная система</p>
          <h1 className="mt-4 max-w-lg text-4xl font-extrabold leading-[1.08] tracking-[-0.045em]">
            Весь цех в одном рабочем контуре
          </h1>
          <p className="mt-4 max-w-md text-sm leading-6 text-[var(--sidebar-muted)]">
            Оперативные данные для сотрудников, кассы и поста погрузки — без лишних экранов и ручных сверок.
          </p>

          <div className="mt-7 grid gap-2">
            {capabilities.map(({ icon: Icon, label, text }) => (
              <div
                key={label}
                className="flex items-center gap-3 rounded-2xl border border-white/[0.07] bg-white/[0.035] p-3"
              >
                <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-white/[0.08] text-[#dceebf]">
                  <Icon className="size-[18px]" />
                </span>
                <div>
                  <div className="text-sm font-bold">{label}</div>
                  <div className="text-xs text-[var(--sidebar-muted)]">{text}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--sidebar-muted)]">
          <span className="size-2 rounded-full bg-[#68b77b]" />
          Защищённый доступ к производственной сети
        </div>
      </aside>

      <main className="app-workspace flex min-h-dvh items-center justify-center px-4 py-8 sm:px-8 lg:px-12">
        <div className={wide ? "w-full max-w-xl" : "w-full max-w-md"}>
          <div className="mb-7 flex items-center gap-3 lg:hidden">
            <span className="flex size-11 items-center justify-center rounded-2xl bg-[var(--card)] shadow-card">
              <Image src="/logo-mark.png" alt="ASYL-LTD" width={30} height={30} className="size-7 object-contain" />
            </span>
            <div>
              <div className="text-sm font-extrabold tracking-[0.04em]">ASYL-LTD</div>
              <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
                Система учёта цеха
              </div>
            </div>
          </div>

          <div className="mb-6">
            <p className="eyebrow">{eyebrow}</p>
            <h1 className="mt-2 text-3xl font-extrabold tracking-[-0.04em] sm:text-4xl">{title}</h1>
            <p className="mt-3 max-w-lg text-sm leading-6 text-[var(--muted-foreground)]">{description}</p>
          </div>

          <section className="rounded-[24px] border bg-[var(--card)] p-5 shadow-float sm:p-7">{children}</section>
        </div>
      </main>
    </div>
  );
}
