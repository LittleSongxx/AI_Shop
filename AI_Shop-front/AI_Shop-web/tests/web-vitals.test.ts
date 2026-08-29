import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { installWebVitalsObserver, type WebVitalMetric } from '@/utils/webVitals';

type EntryList = { getEntries: () => PerformanceEntry[] };

class FakePerformanceObserver {
  static instances: FakePerformanceObserver[] = [];
  readonly callback: (list: EntryList) => void;
  type = '';
  disconnected = false;

  constructor(callback: (list: EntryList) => void) {
    this.callback = callback;
    FakePerformanceObserver.instances.push(this);
  }

  observe(options: { type?: string }) {
    this.type = options.type || '';
  }

  disconnect() {
    this.disconnected = true;
  }

  emit(entries: PerformanceEntry[]) {
    this.callback({ getEntries: () => entries });
  }
}

const originalObserver = globalThis.PerformanceObserver;

describe('Web Vitals observer', () => {
  beforeEach(() => {
    FakePerformanceObserver.instances = [];
    Object.defineProperty(globalThis, 'PerformanceObserver', {
      configurable: true,
      writable: true,
      value: FakePerformanceObserver
    });
  });

  afterEach(() => {
    Object.defineProperty(globalThis, 'PerformanceObserver', {
      configurable: true,
      writable: true,
      value: originalObserver
    });
  });

  it('collects supported browser entries without adding a dependency', () => {
    const metrics: WebVitalMetric[] = [];
    const stop = installWebVitalsObserver((metric) => metrics.push(metric));
    const byType = (type: string) => FakePerformanceObserver.instances.find((item) => item.type === type);

    byType('paint')?.emit([{ name: 'first-contentful-paint', startTime: 120 } as PerformanceEntry]);
    byType('largest-contentful-paint')?.emit([{ name: 'largest-contentful-paint', startTime: 240 } as PerformanceEntry]);
    byType('layout-shift')?.emit([{ duration: 0.1, startTime: 0, value: 0.03 } as PerformanceEntry]);
    byType('event')?.emit([{ duration: 80, startTime: 0 } as PerformanceEntry]);
    byType('navigation')?.emit([
      { requestStart: 10, responseStart: 35, startTime: 0 } as PerformanceNavigationTiming
    ]);

    expect(metrics.map((metric) => metric.name)).toEqual(['FCP', 'LCP', 'CLS', 'INP', 'TTFB']);
    expect(metrics.find((metric) => metric.name === 'TTFB')?.value).toBe(25);
    stop();
    expect(FakePerformanceObserver.instances.every((item) => item.disconnected)).toBe(true);
  });
});
