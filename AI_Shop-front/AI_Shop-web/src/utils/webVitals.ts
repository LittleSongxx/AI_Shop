export type WebVitalName = 'CLS' | 'FCP' | 'INP' | 'LCP' | 'TTFB';

export interface WebVitalMetric {
  name: WebVitalName;
  value: number;
  delta: number;
  id: string;
}

type VitalSink = (metric: WebVitalMetric) => void;
type VitalWindow = Window & { __AI_SHOP_WEB_VITALS__?: WebVitalMetric[] };

const publishMetric: VitalSink = (metric) => {
  const target = window as VitalWindow;
  const metrics = target.__AI_SHOP_WEB_VITALS__ || (target.__AI_SHOP_WEB_VITALS__ = []);
  metrics.push(metric);
  if (metrics.length > 50) metrics.splice(0, metrics.length - 50);
  window.dispatchEvent(new CustomEvent('aishop:web-vital', { detail: metric }));
};

const observe = (
  observers: PerformanceObserver[],
  type: string,
  callback: (entries: PerformanceEntry[]) => void
) => {
  try {
    const observer = new PerformanceObserver((list) => callback(list.getEntries()));
    observer.observe({ type, buffered: true } as PerformanceObserverInit);
    observers.push(observer);
  } catch {
    // Older browsers simply do not expose this metric.
  }
};

export const installWebVitalsObserver = (sink: VitalSink = publishMetric): (() => void) => {
  if (typeof window === 'undefined' || typeof PerformanceObserver === 'undefined') return () => {};

  const observers: PerformanceObserver[] = [];
  const id = (name: WebVitalName) => `${name}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  observe(observers, 'paint', (entries) => {
    const fcp = entries.find((entry) => entry.name === 'first-contentful-paint');
    if (fcp) sink({ name: 'FCP', value: fcp.startTime, delta: fcp.startTime, id: id('FCP') });
  });
  observe(observers, 'navigation', (entries) => {
    const navigation = entries[0] as PerformanceNavigationTiming | undefined;
    if (navigation) {
      const value = Math.max(0, navigation.responseStart - navigation.requestStart);
      sink({ name: 'TTFB', value, delta: value, id: id('TTFB') });
    }
  });
  observe(observers, 'largest-contentful-paint', (entries) => {
    const entry = entries[entries.length - 1];
    if (entry) sink({ name: 'LCP', value: entry.startTime, delta: entry.startTime, id: id('LCP') });
  });

  let cls = 0;
  observe(observers, 'layout-shift', (entries) => {
    for (const entry of entries) {
      const shift = entry as PerformanceEntry & { value?: number; hadRecentInput?: boolean };
      if (!shift.hadRecentInput) cls += Number(shift.value || 0);
    }
    sink({ name: 'CLS', value: cls, delta: cls, id: id('CLS') });
  });

  let inp = 0;
  observe(observers, 'event', (entries) => {
    for (const entry of entries) inp = Math.max(inp, entry.duration || 0);
    sink({ name: 'INP', value: inp, delta: inp, id: id('INP') });
  });

  return () => observers.forEach((observer) => observer.disconnect());
};
