import { fireEvent, render } from '@testing-library/vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AgentProductList from '@/components/agent/AgentProductList.vue';
import { agentApi } from '@/api/modules';

const push = vi.fn();

vi.mock('vue-router', () => ({
  useRouter: () => ({ push })
}));

vi.mock('@/api/modules', () => ({
  agentApi: {
    reportClick: vi.fn()
  }
}));

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ userInfo: { userId: 'u1' } })
}));

vi.mock('@/utils/agentProductConsult', () => ({
  saveAgentConsultProduct: vi.fn()
}));

const products = [
  {
    productId: 'p1',
    productName: '商品一',
    minPrice: 10,
    requestId: '0123456789abcdef0123456789abcdef'
  },
  {
    productId: 'p2',
    productName: '商品二',
    minPrice: 20,
    requestId: '0123456789abcdef0123456789abcdef'
  }
];

describe('agent product click attribution', () => {
  beforeEach(() => {
    push.mockReset();
    vi.mocked(agentApi.reportClick).mockReset().mockResolvedValue({
      requestId: '0123456789abcdef0123456789abcdef',
      productId: 'p2',
      position: 2,
      source: 'hybrid',
      occurredAt: new Date().toISOString()
    });
  });

  it('reports the serving token with a one-based product position', async () => {
    const view = render(AgentProductList, {
      props: { list: products },
      global: {
        stubs: {
          ProductImage: true
        }
      }
    });

    await fireEvent.click(view.getByText('商品二'));

    expect(agentApi.reportClick).toHaveBeenCalledWith(
      'p2',
      '0123456789abcdef0123456789abcdef',
      2
    );
    expect(push).toHaveBeenCalledWith('/product/p2');
  });

  it('does not send an unattributable event for historical cards without a token', async () => {
    const view = render(AgentProductList, {
      props: { list: [{ productId: 'legacy', productName: '旧卡片', minPrice: 8 }] },
      global: {
        stubs: {
          ProductImage: true
        }
      }
    });

    await fireEvent.click(view.getByText('旧卡片'));

    expect(agentApi.reportClick).not.toHaveBeenCalled();
    expect(push).toHaveBeenCalledWith('/product/legacy');
  });
});
