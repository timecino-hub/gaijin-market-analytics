import assert from "node:assert/strict";
import test from "node:test";
import {
  LOCAL_EXTENSION_PAIRING_PAYLOAD_VERSION,
  copyExtensionPairingPayload,
  isExtensionPairingCodeExpired,
  makeExtensionPairingPayload,
  serializeCopyableExtensionPairingPayload,
  serializeExtensionPairingPayload
} from "./extension-pairing-payload.ts";
import type { LocalExtensionPairingCode } from "./types.ts";

test("extension pairing payload includes the pairing response id and code", () => {
  const payload = makeExtensionPairingPayload(pairingCodeFixture());

  assert.deepEqual(payload, {
    version: "local_extension_pairing_v1",
    pairing_code_id: "<id>",
    pairing_code: "<one-time-code>"
  });
  assert.equal(payload.version, LOCAL_EXTENSION_PAIRING_PAYLOAD_VERSION);
});

test("serialized extension pairing payload is compact parseable JSON with only approved fields", () => {
  const serialized = serializeExtensionPairingPayload(makeExtensionPairingPayload(pairingCodeFixture()));
  const parsed = JSON.parse(serialized) as Record<string, unknown>;

  assert.equal(serialized.includes("\n"), false);
  assert.deepEqual(Object.keys(parsed).sort(), ["pairing_code", "pairing_code_id", "version"]);
  assert.equal(parsed.version, "local_extension_pairing_v1");
  assert.equal(parsed.pairing_code_id, "<id>");
  assert.equal(parsed.pairing_code, "<one-time-code>");
});

test("extension pairing payload excludes tokens, urls, and client identity", () => {
  const serialized = serializeExtensionPairingPayload(makeExtensionPairingPayload(pairingCodeFixture()));
  const parsed = JSON.parse(serialized) as Record<string, unknown>;

  for (const disallowed of [
    "token",
    "token_hash",
    "api_url",
    "web_url",
    "client_name",
    "extension_version",
    "source_url"
  ]) {
    assert.equal(disallowed in parsed, false);
  }
});

test("expired pairing codes are not copyable", () => {
  const code = pairingCodeFixture({ expires_at: "2026-07-02T00:00:00.000Z" });

  assert.equal(isExtensionPairingCodeExpired(code, Date.parse("2026-07-02T00:00:00.000Z")), true);
  assert.equal(serializeCopyableExtensionPairingPayload(code, Date.parse("2026-07-02T00:00:01.000Z")), null);
});

test("new pairing codes replace old copyable payloads", () => {
  const first = serializeCopyableExtensionPairingPayload(pairingCodeFixture(), Date.parse("2026-07-02T00:00:00.000Z"));
  const second = serializeCopyableExtensionPairingPayload(
    pairingCodeFixture({ pairing_code_id: "<new-id>", pairing_code: "<new-one-time-code>" }),
    Date.parse("2026-07-02T00:00:00.000Z")
  );

  assert.notEqual(first, second);
  assert.equal(JSON.parse(second ?? "{}").pairing_code_id, "<new-id>");
  assert.equal(JSON.parse(second ?? "{}").pairing_code, "<new-one-time-code>");
});

test("payload helpers do not read browser storage when serializing", () => {
  const localStorageDescriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const sessionStorageDescriptor = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  const indexedDbDescriptor = Object.getOwnPropertyDescriptor(globalThis, "indexedDB");

  Object.defineProperty(globalThis, "localStorage", storageTrap("localStorage"));
  Object.defineProperty(globalThis, "sessionStorage", storageTrap("sessionStorage"));
  Object.defineProperty(globalThis, "indexedDB", storageTrap("indexedDB"));

  try {
    assert.ok(serializeCopyableExtensionPairingPayload(pairingCodeFixture(), Date.parse("2026-07-02T00:00:00.000Z")));
  } finally {
    restoreGlobalProperty("localStorage", localStorageDescriptor);
    restoreGlobalProperty("sessionStorage", sessionStorageDescriptor);
    restoreGlobalProperty("indexedDB", indexedDbDescriptor);
  }
});

test("clipboard copy writes the JSON payload and does not return it on failure", async () => {
  const writes: string[] = [];
  const success = await copyExtensionPairingPayload(
    async (value) => {
      writes.push(value);
    },
    pairingCodeFixture(),
    Date.parse("2026-07-02T00:00:00.000Z")
  );

  const failure = await copyExtensionPairingPayload(
    async () => {
      throw new Error("clipboard denied");
    },
    pairingCodeFixture(),
    Date.parse("2026-07-02T00:00:00.000Z")
  );

  assert.deepEqual(success, { ok: true });
  assert.equal(JSON.parse(writes[0]).version, "local_extension_pairing_v1");
  assert.deepEqual(failure, { ok: false, reason: "clipboard_failed" });
  assert.equal("payload" in failure, false);
});

function pairingCodeFixture(overrides: Partial<LocalExtensionPairingCode> = {}): LocalExtensionPairingCode {
  return {
    pairing_code_id: "<id>",
    pairing_code: "<one-time-code>",
    expires_at: "2026-07-02T00:10:00.000Z",
    ttl_seconds: 600,
    ...overrides
  };
}

function storageTrap(name: string): PropertyDescriptor {
  return {
    configurable: true,
    get() {
      throw new Error(`${name} should not be read`);
    }
  };
}

function restoreGlobalProperty(key: string, descriptor: PropertyDescriptor | undefined) {
  if (descriptor) {
    Object.defineProperty(globalThis, key, descriptor);
  } else {
    Reflect.deleteProperty(globalThis, key);
  }
}
