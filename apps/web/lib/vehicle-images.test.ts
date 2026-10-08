import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { VEHICLE_IMAGES } from "./vehicle-image-catalog.ts";
import { vehicleImageFor } from "./vehicle-images.ts";

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
