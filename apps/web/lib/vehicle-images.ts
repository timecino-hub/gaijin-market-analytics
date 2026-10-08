import { VEHICLE_IMAGES } from "./vehicle-image-catalog.ts";

export type VehicleImageRecord = (typeof VEHICLE_IMAGES)[number];

const imagesByExternalKey = new Map<string, VehicleImageRecord>(
  VEHICLE_IMAGES.map((image) => [image.external_key, image])
);

export function vehicleImageFor(externalKey: string): VehicleImageRecord | null {
  return imagesByExternalKey.get(externalKey) ?? null;
}
