"use client";

import { AppShell } from "@/components/layout/app-shell";
import { ModelTestWorkbench } from "@/components/model-tests/model-test-workbench";
import { RequirePerm } from "@/components/require-perm";

export default function ModelTestsPage() {
  return (
    <RequirePerm perm="" superuserOnly title="Тест моделей">
      <AppShell
        title="Тест моделей"
        section="Управление"
        description="Изолированная проверка detector, color и brand моделей на одном видео без записи в production-аналитику."
      >
        <ModelTestWorkbench />
      </AppShell>
    </RequirePerm>
  );
}
