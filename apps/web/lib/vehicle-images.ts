import { VEHICLE_IMAGES } from "./vehicle-image-catalog.ts";

export type VehicleImageRecord = (typeof VEHICLE_IMAGES)[number];

const imagesByExternalKey = new Map<string, VehicleImageRecord>(
  VEHICLE_IMAGES.map((image) => [image.external_key, image])
);

export function vehicleImageFor(externalKey: string): VehicleImageRecord | null {
  return imagesByExternalKey.get(externalKey) ?? null;
}

export function vehicleDisplayName(externalKey: string, fallback: string): string {
  return (vehicleImageFor(externalKey)?.display_name ?? fallback).replace(/\s+/gu, " ").trim();
}

export function vehicleCategoryLabel(category: string | null | undefined): string | null {
  const label = category?.trim();
  return !label || label === "UNKNOWN" ? null : label === "vehicle" ? "载具" : label;
}
