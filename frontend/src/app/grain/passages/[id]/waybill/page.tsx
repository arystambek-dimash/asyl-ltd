"use client";

import { use } from "react";
import { PassageWaybillPage } from "@/components/grain/passage-waybill";
import { RequirePerm } from "@/components/require-perm";

export default function WaybillPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <RequirePerm perm={["grain.view"]} title="Накладная на отпуск">
      <PassageWaybillPage tripId={Number(id)} />
    </RequirePerm>
  );
}
