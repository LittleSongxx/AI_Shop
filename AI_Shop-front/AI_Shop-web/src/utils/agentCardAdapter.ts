import type { SupportCase } from '@/api/modules';

type UnknownRecord = Record<string, unknown>;

const asRecord = (value: unknown): UnknownRecord | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as UnknownRecord
    : null;

const pick = (record: UnknownRecord, ...keys: string[]): unknown => {
  for (const key of keys) {
    if (record[key] != null && record[key] !== '') return record[key];
  }
  return undefined;
};

const text = (value: unknown): string | undefined => {
  if (value == null) return undefined;
  const normalized = String(value).trim();
  return normalized || undefined;
};

export interface ActionConfirmDetailRow {
  label: string;
  value: string;
}

export interface ActionConfirmOrderItem {
  orderItemId?: string;
  productId?: string;
  productName?: string;
  cover?: string;
  propertyInfo?: string;
  itemAmount?: number | string;
  buyCount?: number | string;
}

export interface ActionConfirmCardData {
  type: 'ACTION_CONFIRM';
  token?: string;
  actionType?: string;
  label?: string;
  summary?: string;
  confirmText?: string;
  riskTip?: string;
  intro?: string;
  status?: number | string;
  statusName?: string;
  snapshotVersion?: string;
  snapshotEtag?: string;
  snapshotHash?: string;
  snapshotCapturedAt?: string;
  orderId?: string;
  orderAmount?: number | string;
  payScene?: string | number;
  items: ActionConfirmOrderItem[];
  details: ActionConfirmDetailRow[];
}

export interface SupportCaseCardData {
  type: 'SUPPORT_CASE_LIST' | 'SUPPORT_CASE_DETAIL';
  cases: SupportCase[];
  case?: SupportCase;
}

const normalizeSupportCase = (raw: unknown): SupportCase | null => {
  const record = asRecord(raw);
  if (!record) return null;
  const caseId = pick(record, 'caseId', 'case_id', 'id');
  if (caseId == null || caseId === '') return null;
  const evidence = asRecord(pick(record, 'evidence', 'evidenceData', 'evidence_data'));
  return {
    caseId: String(caseId),
    caseNo: text(pick(record, 'caseNo', 'case_no')),
    userId: text(pick(record, 'userId', 'user_id')),
    orderId: text(pick(record, 'orderId', 'order_id')),
    orderItemId: text(pick(record, 'orderItemId', 'order_item_id')),
    category: text(pick(record, 'category')),
    categoryLabel: text(pick(record, 'categoryLabel', 'category_label')),
    status: text(pick(record, 'status')),
    description: text(pick(record, 'description')),
    evidence: evidence
      ? {
          path: text(pick(evidence, 'path', 'imagePath', 'image_path')),
          moderationId: Number(pick(evidence, 'moderationId', 'moderation_id')) || undefined,
          moderationStatus: text(pick(evidence, 'moderationStatus', 'moderation_status')),
          scene: text(pick(evidence, 'scene')),
          vlmStatus: text(pick(evidence, 'vlmStatus', 'vlm_status')),
          vlmDescription: text(pick(evidence, 'vlmDescription', 'vlm_description'))
        }
      : undefined,
    supportSessionId: text(pick(record, 'supportSessionId', 'support_session_id')),
    assignedAdmin: text(pick(record, 'assignedAdmin', 'assigned_admin')),
    resolutionCode: text(pick(record, 'resolutionCode', 'resolution_code')),
    rootCause: text(pick(record, 'rootCause', 'root_cause')),
    resolutionSummary: text(pick(record, 'resolutionSummary', 'resolution_summary')),
    createdAt: text(pick(record, 'createdAt', 'created_at')),
    updatedAt: text(pick(record, 'updatedAt', 'updated_at')),
    resolvedAt: text(pick(record, 'resolvedAt', 'resolved_at'))
  };
};

export const normalizeSupportCaseCard = (raw: unknown): SupportCaseCardData | null => {
  const record = asRecord(raw);
  const type = text(record ? pick(record, 'type', 'cardType', 'card_type') : undefined)?.toUpperCase();
  if (type !== 'SUPPORT_CASE_LIST' && type !== 'SUPPORT_CASE_DETAIL') return null;
  const rawCases = pick(record!, 'cases', 'items', 'list');
  const cases = Array.isArray(rawCases)
    ? rawCases.map(normalizeSupportCase).filter((item): item is SupportCase => item !== null)
    : [];
  const detail = normalizeSupportCase(pick(record!, 'case', 'detail', 'supportCase', 'support_case'));
  if (type === 'SUPPORT_CASE_DETAIL' && !detail) return null;
  return { type, cases, ...(detail ? { case: detail } : {}) };
};

const normalizeActionItem = (raw: unknown): ActionConfirmOrderItem | null => {
  const record = asRecord(raw);
  if (!record) return null;
  const productName = text(pick(record, 'productName', 'product_name', 'name'));
  if (!productName) return null;
  return {
    orderItemId: text(pick(record, 'orderItemId', 'order_item_id')),
    productId: text(pick(record, 'productId', 'product_id')),
    productName,
    cover: text(pick(record, 'cover', 'image', 'imageUrl', 'image_url')),
    propertyInfo: text(pick(record, 'propertyInfo', 'property_info', 'sku')),
    itemAmount: pick(record, 'itemAmount', 'item_amount', 'amount') as number | string | undefined,
    buyCount: pick(record, 'buyCount', 'buy_count', 'quantity') as number | string | undefined
  };
};

const normalizeDetail = (raw: unknown): ActionConfirmDetailRow | null => {
  const record = asRecord(raw);
  if (!record) return null;
  const label = text(pick(record, 'label', 'name', 'key'));
  const value = text(pick(record, 'value', 'text', 'content'));
  return label && value ? { label, value } : null;
};

export const normalizeActionConfirmCard = (raw: unknown): ActionConfirmCardData | null => {
  const record = asRecord(raw);
  const type = text(record ? pick(record, 'type', 'cardType', 'card_type') : undefined)?.toUpperCase();
  if (type !== 'ACTION_CONFIRM') return null;
  const rawItems = pick(record!, 'items', 'orderItems', 'order_items');
  const rawDetails = pick(record!, 'details', 'detailRows', 'detail_rows');
  return {
    type: 'ACTION_CONFIRM',
    token: text(pick(record!, 'token', 'actionToken', 'action_token')),
    actionType: text(pick(record!, 'actionType', 'action_type')),
    label: text(pick(record!, 'label', 'title')),
    summary: text(pick(record!, 'summary', 'description')),
    confirmText: text(pick(record!, 'confirmText', 'confirm_text')),
    riskTip: text(pick(record!, 'riskTip', 'risk_tip')),
    intro: text(pick(record!, 'intro', 'hint')),
    status: pick(record!, 'status', 'actionStatus', 'action_status') as number | string | undefined,
    statusName: text(pick(record!, 'statusName', 'status_name')),
    snapshotVersion: text(pick(record!, 'snapshotVersion', 'snapshot_version')),
    snapshotEtag: text(pick(record!, 'snapshotEtag', 'snapshot_etag')),
    snapshotHash: text(pick(record!, 'snapshotHash', 'snapshot_hash')),
    snapshotCapturedAt: text(pick(record!, 'snapshotCapturedAt', 'snapshot_captured_at')),
    orderId: text(pick(record!, 'orderId', 'order_id')),
    orderAmount: pick(record!, 'orderAmount', 'order_amount') as number | string | undefined,
    payScene: pick(record!, 'payScene', 'pay_scene') as string | number | undefined,
    items: Array.isArray(rawItems)
      ? rawItems.map(normalizeActionItem).filter((item): item is ActionConfirmOrderItem => item !== null)
      : [],
    details: Array.isArray(rawDetails)
      ? rawDetails.map(normalizeDetail).filter((item): item is ActionConfirmDetailRow => item !== null)
      : []
  };
};
