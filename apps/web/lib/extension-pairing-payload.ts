import type { LocalExtensionPairingCode } from "./types";

export const LOCAL_EXTENSION_PAIRING_PAYLOAD_VERSION = "local_extension_pairing_v1";

export type ExtensionPairingPayloadV1 = {
  version: typeof LOCAL_EXTENSION_PAIRING_PAYLOAD_VERSION;
  pairing_code_id: string;
  pairing_code: string;
};

export type CopyExtensionPairingPayloadResult =
  | { ok: true }
  | { ok: false; reason: "expired" | "clipboard_failed" };

export function makeExtensionPairingPayload(
  pairingCode: Pick<LocalExtensionPairingCode, "pairing_code_id" | "pairing_code">
): ExtensionPairingPayloadV1 {
  return {
    version: LOCAL_EXTENSION_PAIRING_PAYLOAD_VERSION,
    pairing_code_id: pairingCode.pairing_code_id,
    pairing_code: pairingCode.pairing_code
  };
}

export function serializeExtensionPairingPayload(payload: ExtensionPairingPayloadV1): string {
  return JSON.stringify({
    version: payload.version,
    pairing_code_id: payload.pairing_code_id,
    pairing_code: payload.pairing_code
  });
}

export function serializeCopyableExtensionPairingPayload(
  pairingCode: LocalExtensionPairingCode,
  now = Date.now()
): string | null {
  if (isExtensionPairingCodeExpired(pairingCode, now)) {
    return null;
  }
  return serializeExtensionPairingPayload(makeExtensionPairingPayload(pairingCode));
}

export async function copyExtensionPairingPayload(
  writeText: (value: string) => Promise<void>,
  pairingCode: LocalExtensionPairingCode,
  now = Date.now()
): Promise<CopyExtensionPairingPayloadResult> {
  const payload = serializeCopyableExtensionPairingPayload(pairingCode, now);
  if (!payload) {
    return { ok: false, reason: "expired" };
  }
  try {
    await writeText(payload);
    return { ok: true };
  } catch {
    return { ok: false, reason: "clipboard_failed" };
  }
}

export function isExtensionPairingCodeExpired(
  pairingCode: Pick<LocalExtensionPairingCode, "expires_at">,
  now = Date.now()
): boolean {
  return new Date(pairingCode.expires_at).getTime() <= now;
}
