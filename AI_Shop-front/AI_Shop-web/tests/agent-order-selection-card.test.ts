import { fireEvent, render } from '@testing-library/vue';
import { describe, expect, it, vi } from 'vitest';

import AgentOrderSelectionCard from '@/components/agent/AgentOrderSelectionCard.vue';

const card = {
  type: 'ORDER_SELECTION' as const,
  selectionId: 'sel_123',
  sourceMessageId: '30',
  intent: 'REFUND',
  prompt: '请选择要退款的商品',
  expiresAt: '2099-08-06T16:00:00',
  candidates: [
    {
      targetType: 'ORDER_ITEM' as const,
      targetId: 'SMITEM202608050002',
      orderId: 'SM202608050002',
      orderItemId: 'SMITEM202608050002',
      productName: '索尼 WH-1000XM6 无线降噪耳机',
      amount: 3999,
      orderStatusName: '已付款，待发货',
      orderTime: '2026-08-05 21:01:48'
    }
  ]
};

describe('agent order selection card', () => {
  it('emits only the server-issued selection and locks after success', async () => {
    const onSelect = vi.fn((payload) => payload.done(true));
    const view = render(AgentOrderSelectionCard, {
      props: { card, onSelect },
      global: {
        stubs: {
          ProductImage: true,
          ElIcon: true
        }
      }
    });

    await fireEvent.click(view.getByRole('button', { name: '选择退款' }));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toMatchObject({
      card: { selectionId: 'sel_123', intent: 'REFUND' },
      candidate: {
        targetType: 'ORDER_ITEM',
        targetId: 'SMITEM202608050002'
      }
    });
    expect(view.getByRole('button', { name: '已选择' })).toBeDisabled();
  });

  it('does not allow an expired candidate to be selected', async () => {
    const onSelect = vi.fn();
    const view = render(AgentOrderSelectionCard, {
      props: {
        card: { ...card, expiresAt: '2020-01-01T00:00:00' },
        onSelect
      },
      global: { stubs: { ProductImage: true, ElIcon: true } }
    });

    await fireEvent.click(view.getByRole('button', { name: '选择退款' }));
    expect(onSelect).not.toHaveBeenCalled();
    expect(view.getByText('候选已过期，请重新描述要办理的订单。')).toBeInTheDocument();
  });
});
