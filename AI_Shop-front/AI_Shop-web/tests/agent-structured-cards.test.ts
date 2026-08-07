import { render } from '@testing-library/vue';
import { describe, expect, it, vi } from 'vitest';

import AgentChatItem from '@/components/agent/AgentChatItem.vue';

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() })
}));

vi.mock('@/api/modules', () => ({
  agentApi: { feedback: vi.fn() }
}));

const renderMessage = (assistantMessage: Record<string, unknown>) =>
  render(AgentChatItem, {
    props: {
      data: {
        messageId: 18,
        status: 2,
        assistantMessage: JSON.stringify(assistantMessage)
      }
    },
    global: {
      stubs: {
        ElIcon: true,
        ProductImage: true
      }
    }
  });

describe('agent structured cards', () => {
  it('renders a real-time product comparison without leaking raw JSON', () => {
    const view = renderMessage({
      type: 'PRODUCT_COMPARISON',
      snapshotType: 'REAL_TIME',
      generatedAt: '2026-08-07T06:00:00Z',
      dimensions: ['价格', '库存', '重量'],
      products: [
        { productId: 'p1', productName: '轻薄本 A', minPrice: 5999, availability: 'ON_SALE', properties: [{ name: '重量', value: '1.2kg' }] },
        { productId: 'p2', productName: '轻薄本 B', minPrice: 6999, availability: 'OUT_OF_STOCK', properties: [{ name: '重量', value: '1.3kg' }] }
      ]
    });

    expect(view.getByRole('table')).toHaveTextContent('轻薄本 A');
    expect(view.getByRole('table')).toHaveTextContent('暂时缺货');
    expect(view.queryByText(/"snapshotType"/)).not.toBeInTheDocument();
  });

  it('renders owned support-case details with their evidence status', () => {
    const view = renderMessage({
      type: 'SUPPORT_CASE_DETAIL',
      case: {
        caseId: 7,
        caseNo: 'SC20260807ABC123',
        status: 'IN_PROGRESS',
        categoryLabel: '商品破损',
        description: '外壳有明显破损',
        evidence: { moderationStatus: 'APPROVED' }
      }
    });

    expect(view.getByText('工单详情')).toBeInTheDocument();
    expect(view.getByText('外壳有明显破损')).toBeInTheDocument();
    expect(view.getByText(/图片审核：APPROVED/)).toBeInTheDocument();
  });
});
