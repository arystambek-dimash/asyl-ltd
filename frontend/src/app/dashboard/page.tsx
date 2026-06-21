"use client";
import { AppShell } from "@/components/layout/app-shell";
import { CameraWall } from "@/components/camera-wall";
import { SurveillancePanels } from "@/components/surveillance-panels";

export default function DashboardPage() {
  return (
    <AppShell title="Командный центр">
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_340px]">
        {/* Камеры — основной фокус */}
        <div className="min-w-0">
          <CameraWall />
        </div>
        {/* Боковая панель: сводка, статус камер, очередь, события */}
        <SurveillancePanels />
      </div>
    </AppShell>
  );
}
