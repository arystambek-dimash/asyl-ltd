"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Вагоны переехали на единый пост погрузки — старые закладки не должны падать. */
export default function TrainPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/shipping");
  }, [router]);
  return null;
}
