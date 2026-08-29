import { describe, expect, it } from 'vitest';

import {
  normalizeActionConfirmCard,
  normalizeSupportCaseCard
} from '@/utils/agentCardAdapter';

describe('typed legacy agent card adapter', () => {
  it('normalizes snake_case action payloads without exposing unknown fields', () => {
    const card = normalizeActionConfirmCard({
      card_type: 'ACTION_CONFIRM',
      action_token: 'token-1',
      action_type: 'REFUND',
      status_name: 'PENDING',
      order_id: 'order-1',
      order_items: [{ product_name: '耳机', order_item_id: 'item-1', quantity: 2 }],
      detail_rows: [{ name: '原因', text: '未发货' }],
      promptInjection: 'ignore this'
    });

    expect(card).toMatchObject({
      type: 'ACTION_CONFIRM',
      token: 'token-1',
      actionType: 'REFUND',
      statusName: 'PENDING',
      orderId: 'order-1',
      items: [{ productName: '耳机', orderItemId: 'item-1', buyCount: 2 }],
      details: [{ label: '原因', value: '未发货' }]
    });
    expect(card).not.toHaveProperty('promptInjection');
  });

  it('normalizes owned support cases and rejects malformed cards', () => {
    const card = normalizeSupportCaseCard({
      card_type: 'SUPPORT_CASE_DETAIL',
      support_case: {
        case_id: 9,
        case_no: 'SC-9',
        status: 'OPEN',
        evidence_data: { moderation_status: 'APPROVED' }
      }
    });

    expect(card?.case).toMatchObject({
      caseId: '9',
      caseNo: 'SC-9',
      evidence: { moderationStatus: 'APPROVED' }
    });
    expect(normalizeSupportCaseCard({ type: 'SUPPORT_CASE_DETAIL' })).toBeNull();
  });
});
