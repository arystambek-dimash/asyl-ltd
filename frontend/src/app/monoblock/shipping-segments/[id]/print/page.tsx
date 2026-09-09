"use client";

import { use } from "react";
import { RequirePerm } from "@/components/require-perm";
import { ShippingSegmentPrintPage } from "@/components/shipping/shipping-segment-document";

export default function SegmentPrintPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <RequirePerm perm={["shipping.load", "shipping.view", "train.load", "train.view"]} title="Накладная отрезка">
      <ShippingSegmentPrintPage segmentId={Number(id)} />
    </RequirePerm>
  );
}
