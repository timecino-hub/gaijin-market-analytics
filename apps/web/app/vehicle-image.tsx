import Image from "next/image";
import { vehicleDisplayName, vehicleImageFor } from "../lib/vehicle-images";

type VehicleImageProps = {
  externalKey: string;
  name: string;
  variant?: "thumbnail" | "hero";
};

export function VehicleImage({ externalKey, name, variant = "thumbnail" }: VehicleImageProps) {
  const image = vehicleImageFor(externalKey);
  const displayName = vehicleDisplayName(externalKey, name);

  return (
    <span className={`deco-vehicle-image deco-vehicle-image-${variant}`}>
      {image ? (
        <Image
          src={image.src}
          alt={displayName}
          width={image.width}
          height={image.height}
          unoptimized
          loading="lazy"
          decoding="async"
        />
      ) : (
        <span className="deco-vehicle-image-fallback" aria-hidden="true">
          {displayName.slice(0, variant === "hero" ? 2 : 1).toUpperCase()}
        </span>
      )}
    </span>
  );
}

export function VehicleImageSource({ externalKey }: { externalKey: string }) {
  const image = vehicleImageFor(externalKey);
  return image ? (
    <a className="deco-vehicle-image-source" href={image.source_url} target="_blank" rel="noopener noreferrer">图片来源</a>
  ) : null;
}
