import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { VEHICLE_IMAGES } from "./vehicle-image-catalog.ts";
import { vehicleCategoryLabel, vehicleDisplayName, vehicleImageFor } from "./vehicle-images.ts";

test("reviewed vehicle display names replace raw filenames using exact identities", () => {
  assert.equal(vehicleDisplayName("id50228_spz_12_3_lgs_germany", "id50228_spz_12_3_lgs_germany"), "SPz 12-3 LGS");
  assert.equal(vehicleDisplayName("id50145_e_b_r__1963_france", "raw filename"), "E.B.R. (1963)");
  assert.equal(vehicleDisplayName("items_Merkava Mk.3D (USA)", "raw filename"), "Merkava Mk.3D (USA)");
  assert.equal(vehicleDisplayName("M8", "M8"), "M8 LAC");
  assert.equal(vehicleDisplayName("75mm Gun Motor Carriage M8", "M8"), "M8A1 GMC");
});

test("unmapped identities retain their supplied name without guessing a vehicle", () => {
  assert.equal(vehicleDisplayName("unmapped-vehicle", "  Variant\u00a0 name\t (Japan)  "), "Variant name (Japan)");
  assert.equal(vehicleDisplayName(" id50228_spz_12_3_lgs_germany", "Custom variant"), "Custom variant");
  assert.equal(vehicleDisplayName("id50228_spz_12_3_lgs_germany-unknown", "Unknown model"), "Unknown model");
});

test("unknown category labels are omitted without relabelling known categories", () => {
  for (const value of [null, undefined, "", "  ", "UNKNOWN", " UNKNOWN "]) {
    assert.equal(vehicleCategoryLabel(value), null);
  }
  assert.equal(vehicleCategoryLabel("坦克"), "坦克");
});

test("vehicle pictures match only their exact catalog identity", () => {
  assert.equal(VEHICLE_IMAGES.length, 55);
  assert.equal(new Set(VEHICLE_IMAGES.map((image) => image.external_key)).size, VEHICLE_IMAGES.length);
  for (const image of VEHICLE_IMAGES) {
    assert.equal(vehicleImageFor(image.external_key), image);
    assert.equal(vehicleImageFor(` ${image.external_key}`), null);
    assert.equal(vehicleImageFor(`${image.external_key} `), null);
    assert.equal(vehicleImageFor(`${image.external_key}-unknown`), null);
  }
  assert.equal(vehicleImageFor(""), null);
  assert.equal(vehicleImageFor("unmapped-vehicle"), null);
});

test("every mapped picture is a local original PNG with verified dimensions and checksum", async () => {
  for (const image of VEHICLE_IMAGES) {
    assert.match(image.src, /^\/vehicle-images\/[A-Za-z0-9_-]+-[a-f0-9]{12}\.png$/);
    assert.equal(new URL(image.source_url).hostname, "wiki.warthunder.com");
    assert.ok(image.display_name.length > 0);
    assert.equal(image.width, 512);
    assert.equal(image.height, 256);
    const bytes = await readFile(new URL(`../public${image.src}`, import.meta.url));
    assert.equal(bytes.subarray(0, 8).toString("hex"), "89504e470d0a1a0a");
    assert.equal(bytes.readUInt32BE(16), image.width);
    assert.equal(bytes.readUInt32BE(20), image.height);
    assert.equal(createHash("sha256").update(bytes).digest("hex"), image.sha256);
    assert.ok(image.src.includes(image.sha256.slice(0, 12)));
  }
});
