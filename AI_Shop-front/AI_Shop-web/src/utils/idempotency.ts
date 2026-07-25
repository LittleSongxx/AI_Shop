const KEY_PATTERN = /^[A-Za-z0-9._:-]{16,64}$/;
const STORAGE_PREFIX = 'simlect:idempotency:';

type StoredKey = {
  fingerprint: string;
  key: string;
};

function normalize(value: unknown, seen: Set<object>): unknown {
  if (value === null || typeof value !== 'object') {
    return value === undefined ? null : value;
  }
  if (seen.has(value)) {
    throw new TypeError('Cannot fingerprint a cyclic payload');
  }
  seen.add(value);
  try {
    if (Array.isArray(value)) {
      return value.map((item) => normalize(item, seen));
    }
    return Object.keys(value as Record<string, unknown>)
      .sort()
      .reduce<Record<string, unknown>>((result, key) => {
        result[key] = normalize((value as Record<string, unknown>)[key], seen);
        return result;
      }, {});
  } finally {
    seen.delete(value);
  }
}

export function payloadFingerprint(payload: unknown): string {
  const canonical = JSON.stringify(normalize(payload, new Set<object>()));
  let hash = 2166136261;
  for (let index = 0; index < canonical.length; index += 1) {
    hash ^= canonical.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

function createKey(): string {
  const browserCrypto = globalThis.crypto;
  if (browserCrypto?.randomUUID) {
    return browserCrypto.randomUUID();
  }
  if (browserCrypto?.getRandomValues) {
    const bytes = new Uint8Array(16);
    browserCrypto.getRandomValues(bytes);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  const fallback = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random()
    .toString(36)
    .slice(2)}`;
  return fallback
    .replace(/[^A-Za-z0-9._:-]/g, '')
    .padEnd(16, '0')
    .slice(0, 64);
}

function storageKey(scope: string): string {
  return `${STORAGE_PREFIX}${scope}`;
}

function readStored(scope: string): StoredKey | null {
  try {
    const storage = globalThis.sessionStorage;
    const raw = storage?.getItem(storageKey(scope));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<StoredKey>;
    if (
      typeof value.fingerprint !== 'string' ||
      typeof value.key !== 'string' ||
      !KEY_PATTERN.test(value.key)
    ) {
      return null;
    }
    return { fingerprint: value.fingerprint, key: value.key };
  } catch {
    return null;
  }
}

function writeStored(scope: string, value: StoredKey): void {
  try {
    globalThis.sessionStorage?.setItem(storageKey(scope), JSON.stringify(value));
  } catch {
    // A blocked or full sessionStorage must not block checkout.
  }
}

export function getOrCreateIdempotencyKey(scope: string, payload: unknown): string {
  const fingerprint = payloadFingerprint(payload);
  const stored = readStored(scope);
  if (stored?.fingerprint === fingerprint) {
    return stored.key;
  }
  const key = createKey();
  writeStored(scope, { fingerprint, key });
  return key;
}

export function clearIdempotencyKey(scope: string, payload?: unknown): void {
  try {
    if (payload !== undefined) {
      const stored = readStored(scope);
      if (!stored || stored.fingerprint !== payloadFingerprint(payload)) return;
    }
    globalThis.sessionStorage?.removeItem(storageKey(scope));
  } catch {
    // Ignore storage failures; the next payload fingerprint will rotate the key.
  }
}

export const IDEMPOTENCY_KEY_PATTERN = KEY_PATTERN;
