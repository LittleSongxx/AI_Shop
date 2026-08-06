export interface RecommendationAttribution {
  requestId: string;
  productId: string;
  position: number;
  source: string;
  occurredAt: string;
}

interface StoredRecommendationAttribution extends RecommendationAttribution {
  userId: string;
}

const STORAGE_KEY = 'eshop_ai_recommendation_attributions';
export const RECOMMENDATION_ATTRIBUTION_TTL_MS = 7 * 24 * 60 * 60 * 1000;

function readAll(): StoredRecommendationAttribution[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function isValid(
  value: Partial<StoredRecommendationAttribution>,
  now = Date.now()
): value is StoredRecommendationAttribution {
  const occurredAt = Date.parse(String(value.occurredAt || ''));
  return (
    !!value.userId &&
    !!value.requestId &&
    !!value.productId &&
    Number.isInteger(value.position) &&
    Number(value.position) >= 1 &&
    !!value.source &&
    Number.isFinite(occurredAt) &&
    occurredAt <= now + 60_000 &&
    now - occurredAt <= RECOMMENDATION_ATTRIBUTION_TTL_MS
  );
}

function writeAll(values: StoredRecommendationAttribution[]) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(values));
  } catch {
    // Attribution is optional; storage pressure must not affect navigation.
  }
}

export function saveRecommendationAttribution(
  attribution: RecommendationAttribution,
  userId: string
): boolean {
  const candidate: StoredRecommendationAttribution = {
    ...attribution,
    userId: String(userId || '').trim()
  };
  if (!isValid(candidate)) return false;

  const now = Date.now();
  const active = readAll().filter((item) => isValid(item, now));
  const previous = active.find(
    (item) => item.userId === candidate.userId && item.productId === candidate.productId
  );
  if (previous && Date.parse(previous.occurredAt) >= Date.parse(candidate.occurredAt)) {
    writeAll(active);
    return true;
  }
  writeAll([
    ...active.filter(
      (item) => !(item.userId === candidate.userId && item.productId === candidate.productId)
    ),
    candidate
  ]);
  return true;
}

export function loadRecommendationAttribution(
  userId: string | undefined,
  productId: string | undefined
): RecommendationAttribution | null {
  const normalizedUserId = String(userId || '').trim();
  const normalizedProductId = String(productId || '').trim();
  if (!normalizedUserId || !normalizedProductId) return null;
  const now = Date.now();
  const active = readAll().filter((item) => isValid(item, now));
  writeAll(active);
  const found = active.find(
    (item) => item.userId === normalizedUserId && item.productId === normalizedProductId
  );
  if (!found) return null;
  const { userId: _userId, ...attribution } = found;
  return attribution;
}

export function recommendationAttributionCommandFields(
  attribution: RecommendationAttribution | null | undefined
) {
  if (!attribution) return {};
  return {
    aiRequestId: attribution.requestId,
    aiPosition: attribution.position
  };
}

export function clearRecommendationAttributions() {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore browsers where storage is unavailable.
  }
}
