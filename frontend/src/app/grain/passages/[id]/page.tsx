import { GrainTripDetail } from "@/components/grain/trip-detail";

export default function TripPage(props: { params: Promise<{ id: string }> }) {
  return <GrainTripDetail {...props} direction="passage" />;
}
