import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  clearRecommendationAttributions,
  loadRecommendationAttribution,
  saveRecommendationAttribution
} from '@/utils/recommendationAttribution';

describe('recommendation attribution session', () => {
  beforeEach(() => {
    clearRecommendationAttributions();
    vi.useRealTimers();
  });

  it('restores only for the same user and product', () => {
    const attribution = {
      requestId: 'request-1',
      productId: 'product-1',
      position: 2,
      source: 'hybrid',
      occurredAt: new Date().toISOString()
    };

    expect(saveRecommendationAttribution(attribution, 'user-1')).toBe(true);
    expect(loadRecommendationAttribution('user-1', 'product-1')).toEqual(attribution);
    expect(loadRecommendationAttribution('user-2', 'product-1')).toBeNull();
    expect(loadRecommendationAttribution('user-1', 'product-2')).toBeNull();
  });

  it('expires touchpoints after seven days', () => {
    const now = new Date('2026-08-06T09:00:00+08:00').getTime();
    vi.useFakeTimers();
    vi.setSystemTime(now);
    expect(
      saveRecommendationAttribution(
        {
          requestId: 'request-old',
          productId: 'product-1',
          position: 1,
          source: 'hybrid',
          occurredAt: new Date(now - 8 * 24 * 60 * 60 * 1000).toISOString()
        },
        'user-1'
      )
    ).toBe(false);
    expect(loadRecommendationAttribution('user-1', 'product-1')).toBeNull();
  });

  it('keeps the later validated click for a product', () => {
    const later = new Date().toISOString();
    const earlier = new Date(Date.now() - 60_000).toISOString();
    saveRecommendationAttribution(
      {
        requestId: 'request-later',
        productId: 'product-1',
        position: 1,
        source: 'personalized',
        occurredAt: later
      },
      'user-1'
    );
    saveRecommendationAttribution(
      {
        requestId: 'request-earlier',
        productId: 'product-1',
        position: 3,
        source: 'hybrid',
        occurredAt: earlier
      },
      'user-1'
    );

    expect(loadRecommendationAttribution('user-1', 'product-1')?.requestId).toBe(
      'request-later'
    );
  });
});
