import type { FactoryZone, GrainSilo, GrainWagon, StockItem } from "@/lib/types";
import { CameraAsset } from "./camera";
import { ConveyorAsset } from "./conveyor";
import { DockAsset } from "./dock";
import { GateAsset } from "./gate";
import { MillAsset } from "./mill";
import { ParkingAsset } from "./parking";
import { RailAsset } from "./rail";
import { ScaleAsset } from "./scale";
import { SiloParkAsset } from "./silo-park";
import { WarehouseAsset } from "./warehouse";
import { CanteenAsset, LabAsset, OfficeAsset, UtilityAsset } from "./buildings";

/** Живые данные, которыми карта оживляет ассеты. */
export type FactoryLive = {
  silos: GrainSilo[] | null;
  stock: StockItem[] | null;
  wagons: GrainWagon[] | null;
};

/** Рисует участок карты подходящим ассетом в боксе (0,0)…(width,height). */
export function ZoneAsset({ zone, live }: { zone: FactoryZone; live: FactoryLive }) {
  const { width, height, name, note } = zone;
  switch (zone.kind) {
    case "silos":
      return <SiloParkAsset zoneId={zone.id} width={width} height={height} name={name} silos={live.silos} />;
    case "warehouse":
      return <WarehouseAsset width={width} height={height} name={name} stock={live.stock} />;
    case "production":
      return <MillAsset width={width} height={height} name={name} note={note} />;
    case "gate":
      return <GateAsset width={width} height={height} name={name} />;
    case "scale":
      return <ScaleAsset width={width} height={height} name={name} note={note} />;
    case "parking":
      return <ParkingAsset width={width} height={height} name={name} />;
    case "dock":
      return <DockAsset width={width} height={height} name={name} />;
    case "conveyor":
      return <ConveyorAsset width={width} height={height} />;
    case "rail":
      return <RailAsset width={width} height={height} name={name} />;
    case "camera":
      return <CameraAsset width={width} height={height} hasStream={Boolean(zone.note.trim())} />;
    case "canteen":
      return <CanteenAsset width={width} height={height} name={name} note={note} />;
    case "office":
      return <OfficeAsset width={width} height={height} name={name} note={note} />;
    case "lab":
      return <LabAsset width={width} height={height} name={name} note={note} />;
    case "utility":
    default:
      return <UtilityAsset width={width} height={height} name={name} note={note} />;
  }
}
