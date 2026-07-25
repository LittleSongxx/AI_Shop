import { beforeEach, describe, expect, it } from 'vitest';
import {
  clearIdempotencyKey,
  getOrCreateIdempotencyKey,
  IDEMPOTENCY_KEY_PATTERN,
  payloadFingerprint
} from '@/utils/idempotency';

describe('checkout idempotency keys', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('fingerprints object key order deterministically', () => {
    expect(payloadFingerprint({ b: 2, a: 1 })).toBe(payloadFingerprint({ a: 1, b: 2 }));
  });

  it('reuses a key for the same payload and rotates it after a payload change', () => {
    const first = getOrCreateIdempotencyKey('order.post', { addressId: 'a', amount: 10 });
    const replay = getOrCreateIdempotencyKey('order.post', { amount: 10, addressId: 'a' });
    const changed = getOrCreateIdempotencyKey('order.post', { addressId: 'b', amount: 10 });

    expect(first).toBe(replay);
    expect(changed).not.toBe(first);
    expect(IDEMPOTENCY_KEY_PATTERN.test(first)).toBe(true);
    expect(IDEMPOTENCY_KEY_PATTERN.test(changed)).toBe(true);
  });

  it('clears only the matching payload key', () => {
    const payload = { productId: 'p-1', quantity: 1 };
    const key = getOrCreateIdempotencyKey('coupon.rush', payload);
    clearIdempotencyKey('coupon.rush', { productId: 'p-2', quantity: 1 });
    expect(getOrCreateIdempotencyKey('coupon.rush', payload)).toBe(key);
    clearIdempotencyKey('coupon.rush', payload);
    expect(getOrCreateIdempotencyKey('coupon.rush', payload)).not.toBe(key);
  });
});
