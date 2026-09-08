"use client";

import { GrainToolbar } from "@/components/grain/grain-toolbar";
import { UnassignedWeighingsPanel } from "@/components/grain/unassigned-weighings";
import { VehiclePlateCameraWorkspace } from "@/components/grain/vehicle-plate-camera";
import { PassageHistory } from "@/components/grain/passage-history";
import { WagonNumberCameraWorkspace } from "@/components/grain/wagon-number-camera";
import { FlowEmptyState, WagonTable } from "@/components/grain/wagon-table";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { LoadMore } from "@/components/ui/load-more";
import { Modal } from "@/components/ui/modal";
import { Tabs } from "@/components/ui/tabs";
import { can } from "@/lib/can";
import { formatKg, grainTripHref, grainWorkspaceHref } from "@/lib/grain";
import type { GrainSupply, GrainWagon } from "@/lib/types";
import { useDebounced } from "@/lib/use-debounced";
import { useLocalDay } from "@/lib/use-local-day";
import { usePagedApi } from "@/lib/use-paged-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { useAuth } from "@/store/auth";
import { ArrowRight, ScanLine, Search, TrainFront, Truck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ArrivalForm } from "./arrival-form";
import { PassageForm } from "./passage-form";
import { SupplyForm } from "./supply-form";

type GrainTab = "expected" | "on_site" | "finished" | "camera";
type GrainDirection = GrainWagon["direction"];

const DIRECTION_TABS = [
  { key: "intake", label: "Приход", icon: TrainFront },
  { key: "passage", label: "Вывоз", icon: Truck },
];

const INTAKE_TABS = [
  { key: "expected", label: "Ожидаются" },
  { key: "on_site", label: "На территории" },
  { key: "finished", label: "Завершённые" },
  { key: "camera", label: "Камера проходной", icon: ScanLine },
];

const PASSAGE_TABS = [
  { key: "on_site", label: "На территории" },
  { key: "finished", label: "Завершённые" },
  { key: "camera", label: "Камера проходной", icon: ScanLine },
];

/**
 * Адрес списка рейсов. scope и direction идут первыми, фильтры — только
 * когда заданы. День завершённых — это один календарный день: date_from и
 * date_to совпадают, бэкенд сравнивает с датой выезда.
 */
function wagonsUrl(tab: GrainTab, direction: GrainDirection, finishedDay: string, search: string): string | null {
  if (tab !== "on_site" && tab !== "finished") return null;
  const query = new URLSearchParams({ scope: tab, direction });
  if (tab === "finished" && finishedDay) {
    query.set("date_from", finishedDay);
    query.set("date_to", finishedDay);
  }
  if (search) query.set("search", search);
  return `${direction === "passage" ? "/grain/passages/" : "/grain/wagons/"}?${query.toString()}`;
}

const EMPTY_LIST_TEXT = {
  intake: { on_site: "На территории нет поездов на приём", finished: "Завершённых приходов пока нет" },
  passage: { on_site: "На территории нет машин на вывоз", finished: "Завершённых вывозов пока нет" },
} as const;

/** Пустая таблица объясняет, почему пусто: фильтр, день или действительно нет рейсов. */
function wagonsEmptyText(direction: GrainDirection, tab: GrainTab, finishedDay: string, search: string): string {
  if (search) return "По запросу ничего не найдено";
  if (tab === "finished" && finishedDay) return "За этот день завершённых рейсов нет";
  return EMPTY_LIST_TEXT[direction][tab === "finished" ? "finished" : "on_site"];
}

function ExpectedIntakes({
  supplies,
  canArrive,
  onArrival,
}: {
  supplies: GrainSupply[];
  canArrive: boolean;
  onArrival: (supply: GrainSupply) => void;
}) {
  if (!supplies.length) {
    return <FlowEmptyState text="Ожидаемых приходов нет — создайте «Новый приход»" />;
  }

  return (
    <div className="grid gap-3 xl:grid-cols-2">
      {supplies.map((supply) => {
        const wagon = supply.wagons[0];
        return (
          <article
            key={supply.id}
            className="group relative overflow-hidden rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_10px_32px_rgba(15,23,42,.06)] transition hover:-translate-y-0.5 hover:shadow-[0_16px_38px_rgba(15,23,42,.1)]"
          >
            <div
              className="absolute inset-y-0 left-0 w-1.5"
              style={{ backgroundColor: supply.grain_type_color || "#B78132" }}
            />
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">Приход #{supply.id}</p>
                <h3 className="mt-1 truncate text-lg font-bold tracking-tight text-slate-900">{supply.supplier}</h3>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Badge tone="warning" dot>
                    Ожидает камеру
                  </Badge>
                  <span className="text-xs text-slate-500">{supply.grain_type_name}</span>
                </div>
              </div>
              <span className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-amber-50 text-amber-700">
                <TrainFront className="size-5" />
              </span>
            </div>
            <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-3">
              <div className="rounded-xl bg-slate-50 p-3">
                <p className="text-[10px] uppercase tracking-wide text-slate-400">Ожидаемый вес</p>
                <p className="mt-1 font-bold tabular-nums">{formatKg(supply.expected_total_kg)}</p>
              </div>
              <div className="rounded-xl bg-slate-50 p-3 sm:col-span-2">
                <p className="text-[10px] uppercase tracking-wide text-slate-400">Назначенный силос</p>
                <p className="mt-1 truncate font-bold">{supply.assigned_silo_name || "—"}</p>
              </div>
            </div>
            <div className="mt-4 flex items-center justify-between gap-3 border-t border-slate-100 pt-4">
              <span className="flex items-center gap-2 text-xs text-slate-400">
                <ScanLine className="size-4" /> {wagon?.number || "Номер ещё не получен"}
              </span>
              {canArrive && (
                <Button size="sm" onClick={() => onArrival(supply)}>
                  Принять поезд <ArrowRight className="size-4" />
                </Button>
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function GrainPageInner({ initialDirection }: { initialDirection: GrainDirection }) {
  const router = useRouter();
  const { me } = useAuth();
  const canSupply = can(me, "grain.supply");
  const canArrive = can(me, "grain.arrive");
  const canWeigh = can(me, "grain.weigh");
  const [direction, setDirection] = useState<GrainDirection>(initialDirection);
  const [tabByDirection, setTabByDirection] = useState<Record<GrainDirection, GrainTab>>({
    intake: "on_site",
    passage: "on_site",
  });
  const tab = tabByDirection[direction];
  const [supplyOpen, setSupplyOpen] = useState(false);
  const [arriveOpen, setArriveOpen] = useState(false);
  const [passageOpen, setPassageOpen] = useState(false);
  const [arrivalSupply, setArrivalSupply] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  // Завершённые по умолчанию идут за календарём: null — «сегодня» по часам
  // страницы (после полуночи день сменится сам), '' — все дни, строка —
  // явно выбранный день.
  const today = useLocalDay();
  const [finishedDay, setFinishedDay] = useState<string | null>(null);
  const effectiveFinishedDay = finishedDay ?? today;
  const [search, setSearch] = useState("");
  // Поиск не должен дёргать API на каждую букву.
  const debouncedSearch = useDebounced(search.trim());
  const listTab = tab === "on_site" || tab === "finished";
  // Архив («Все дни») не опрашиваем: опрос сбрасывал бы список на первую
  // страницу и схлопывал «Показать ещё» во время просмотра старых дней.
  const pollWagons = listTab && !(tab === "finished" && !effectiveFinishedDay);

  const supplies = usePagedApi<GrainSupply>(
    direction === "intake" && tab === "expected" ? "/grain/supplies/?status=expected&awaiting_arrival=1" : null,
    50,
  );
  const wagons = usePagedApi<GrainWagon>(wagonsUrl(tab, direction, effectiveFinishedDay, debouncedSearch), 50);
  const arrivalSupplies = usePagedApi<GrainSupply>(
    arriveOpen ? "/grain/supplies/?status=expected&awaiting_arrival=1" : null,
    100,
  );
  useVisiblePolling(wagons.reload, 10_000, pollWagons);

  /** Выбор сегодняшней даты возвращает режим «за календарём», а не замораживает день. */
  function pickFinishedDay(value: string) {
    setFinishedDay(value === today ? null : value);
  }

  function refreshAll() {
    void supplies.reload();
    void wagons.reload();
    void arrivalSupplies.reload();
  }

  function selectDirection(next: GrainDirection) {
    if (next !== direction) {
      setNotice("");
      router.push(grainWorkspaceHref(next));
    }
    setDirection(next);
  }

  function selectStatusTab(key: string) {
    setDirectionTab(direction, key as GrainTab);
  }

  function setDirectionTab(nextDirection: GrainDirection, nextTab: GrainTab) {
    setTabByDirection((current) => ({ ...current, [nextDirection]: nextTab }));
  }

  function openArrival(supply?: GrainSupply) {
    selectDirection("intake");
    setArrivalSupply(supply?.id ?? null);
    setArriveOpen(true);
  }

  function openPassage() {
    selectDirection("passage");
    setPassageOpen(true);
  }

  function changeDirection(key: string) {
    if (key === "intake" || key === "passage") selectDirection(key);
  }

  return (
    <AppShell
      title="Приход и вывоз"
      section="Работа"
      description={
        direction === "intake"
          ? "Приход: поезд привозит зерно. Вагонные весы пока не подключены; весы машин вывоза здесь не используются."
          : "Вывоз можно оформлять автоматически по стабильному весу и номеру камеры либо вручную. Доступность автоматики показана во вкладке «Камера проходной»."
      }
      actions={
        <GrainToolbar
          direction={direction}
          canArrive={canArrive}
          canSupply={canSupply}
          canWeigh={canWeigh}
          onPassage={openPassage}
          onArrival={() => openArrival()}
          onSupply={() => {
            selectDirection("intake");
            setSupplyOpen(true);
          }}
        />
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-[var(--card)] p-2">
          <Tabs
            tabs={DIRECTION_TABS}
            active={direction}
            onChange={changeDirection}
            variant="segment"
            label="Направление рейса"
            className="w-full sm:w-auto [&>button]:flex-1 sm:[&>button]:min-w-36"
          />
          <p className="hidden pr-2 text-xs text-[var(--muted-foreground)] lg:block">
            {direction === "intake" ? "Таблица поездов на приём зерна" : "Таблица машин на вывоз груза"}
          </p>
        </div>

        <Tabs
          tabs={direction === "intake" ? INTAKE_TABS : PASSAGE_TABS}
          active={tab}
          onChange={selectStatusTab}
          label="Статус рейсов"
        />

        {listTab && (
          <div className="flex flex-wrap items-center gap-2">
            {tab === "finished" && (
              <>
                <Input
                  type="date"
                  aria-label="День"
                  value={effectiveFinishedDay}
                  onChange={(event) => pickFinishedDay(event.target.value)}
                  className="w-auto"
                />
                <Button variant="ghost" size="sm" disabled={!effectiveFinishedDay} onClick={() => setFinishedDay("")}>
                  Все дни
                </Button>
              </>
            )}
            <div className="relative w-full sm:ml-auto sm:w-72">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
              <Input
                type="search"
                aria-label="Поиск"
                className="pl-8"
                placeholder="Номер, груз, поставщик"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
          </div>
        )}

        {notice && (
          <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            {notice}
          </p>
        )}

        {direction === "passage" && tab !== "camera" && (
          <UnassignedWeighingsPanel canWeigh={canWeigh} onChanged={refreshAll} />
        )}

        {direction === "passage" && <PassageHistory />}

        {tab === "camera" ? (
          direction === "intake" ? (
            <WagonNumberCameraWorkspace canManage={Boolean(me?.is_superuser)} />
          ) : (
            <VehiclePlateCameraWorkspace />
          )
        ) : tab === "expected" ? (
          <>
            {supplies.error && <ErrorAlert message={supplies.error} onRetry={refreshAll} />}
            <ExpectedIntakes supplies={supplies.items} canArrive={canArrive} onArrival={openArrival} />
            <LoadMore
              shown={supplies.items.length}
              total={supplies.count}
              hasMore={supplies.hasMore}
              loading={supplies.loadingMore}
              onClick={supplies.loadMore}
            />
          </>
        ) : (
          <>
            {wagons.error && <ErrorAlert message={wagons.error} onRetry={() => void wagons.reload()} />}
            {/* Первая загрузка списка: пока строк нет, показываем заглушку, а не
                «ничего не найдено». При опросе строки уже есть — таблица остаётся. */}
            {wagons.loading && wagons.items.length === 0 ? (
              <DataGate loading />
            ) : (
              <WagonTable
                wagons={wagons.items}
                me={me}
                direction={direction}
                emptyText={wagonsEmptyText(direction, tab, effectiveFinishedDay, debouncedSearch)}
                onDeleted={() => {
                  setNotice(tab === "finished" ? "Рейс удалён, остаток силоса пересчитан." : "Активный рейс удалён.");
                  refreshAll();
                }}
              />
            )}
            <LoadMore
              shown={wagons.items.length}
              total={wagons.count}
              hasMore={wagons.hasMore}
              loading={wagons.loadingMore}
              onClick={wagons.loadMore}
            />
          </>
        )}
      </div>

      <Modal
        open={supplyOpen}
        onClose={() => setSupplyOpen(false)}
        eyebrow="Приход · 4 поля"
        title="Новый приход зерна"
        description="Создайте ожидаемый поезд и сразу задайте его конечный силос."
        className="max-w-2xl"
      >
        {supplyOpen && (
          <SupplyForm
            onCancel={() => setSupplyOpen(false)}
            onDone={() => {
              setSupplyOpen(false);
              setDirection("intake");
              setDirectionTab("intake", "expected");
              setNotice("Приход создан. Ожидаем номер от камеры проходной.");
              refreshAll();
            }}
          />
        )}
      </Modal>

      <Modal
        open={arriveOpen}
        onClose={() => setArriveOpen(false)}
        eyebrow="Проходная · Камера"
        title="Номер поезда получен"
        description="Свяжите распознанный номер с ожидаемым приходом."
        className="max-w-lg"
      >
        {arriveOpen && (
          <ArrivalForm
            supplies={arrivalSupplies.items}
            initialSupply={arrivalSupply}
            onCancel={() => setArriveOpen(false)}
            onDone={(wagon) => {
              setArriveOpen(false);
              setDirection("intake");
              setNotice(`Поезд ${wagon.number} зарегистрирован. Вагонные весы пока не подключены.`);
              setDirectionTab("intake", "on_site");
              refreshAll();
            }}
          />
        )}
      </Modal>

      <Modal
        open={passageOpen}
        onClose={() => setPassageOpen(false)}
        eyebrow="Резервный режим · Вывоз"
        title="Оформить вывоз"
        description="Используйте ручное оформление, если автоматика не создала или не продолжила рейс."
        className="max-w-lg"
      >
        {passageOpen && (
          <PassageForm
            onCancel={() => setPassageOpen(false)}
            onDone={(wagon) => {
              setPassageOpen(false);
              setDirection("passage");
              setNotice(`Вывоз ${wagon.number || `#${wagon.id}`} оформлен — взвесьте пустую машину на въезде.`);
              setDirectionTab("passage", "on_site");
              refreshAll();
              router.push(grainTripHref(wagon));
            }}
          />
        )}
      </Modal>
    </AppShell>
  );
}

export function GrainWorkspace({ direction }: { direction: GrainDirection }) {
  return (
    <RequirePerm perm="grain.view" title="Приход и вывоз">
      <GrainPageInner key={direction} initialDirection={direction} />
    </RequirePerm>
  );
}
