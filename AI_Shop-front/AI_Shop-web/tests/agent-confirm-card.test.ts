import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AgentConfirmCard from '@/components/agent/AgentConfirmCard.vue';
import { agentApi } from '@/api/modules';

vi.mock('@/api/modules', () => ({
  agentApi: {
    confirmAction: vi.fn(),
    cancelAction: vi.fn()
  }
}));

vi.mock('@/utils/toast', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn()
  }
}));

describe('agent confirmation card', () => {
  beforeEach(() => {
    cleanup();
    vi.mocked(agentApi.confirmAction).mockReset();
    vi.mocked(agentApi.cancelAction).mockReset();
  });

  it('persists the confirmed visual state after a successful action', async () => {
    vi.mocked(agentApi.confirmAction).mockResolvedValue({
      success: true,
      resultMessage: '订单已确认收货'
    });
    const view = render(AgentConfirmCard, {
      props: {
        card: {
          token: 'act_1234567890123456',
          label: '确认收货',
          confirmText: '确认收货',
          status: 0
        }
      }
    });

    await fireEvent.click(view.getByRole('button', { name: '确认收货' }));

    await waitFor(() => {
      expect(view.getByText('订单已确认收货')).toBeInTheDocument();
      expect(view.getByText('已确认执行')).toBeInTheDocument();
    });
    expect(view.queryByRole('button', { name: '确认收货' })).not.toBeInTheDocument();
  });

  it('shows executing and expired states without exposing action buttons', () => {
    const executing = render(AgentConfirmCard, {
      props: {
        card: {
          token: 'act_executing',
          label: '退款申请',
          status: 3
        }
      }
    });
    expect(executing.getByText('执行中，请勿重复操作')).toBeInTheDocument();
    expect(executing.queryByRole('button', { name: '确认提交' })).not.toBeInTheDocument();
    executing.unmount();

    const expired = render(AgentConfirmCard, {
      props: {
        card: {
          token: 'act_expired',
          label: '退款申请',
          status: 5
        }
      }
    });
    expect(expired.getByText('已过期，请重新发起')).toBeInTheDocument();
    expect(expired.queryByRole('button', { name: '确认提交' })).not.toBeInTheDocument();
  });

  it('does not mark an action cancelled when the server returns success=false', async () => {
    vi.mocked(agentApi.cancelAction).mockResolvedValue({
      success: false,
      resultMessage: '操作处理中，请稍后再试'
    });
    const view = render(AgentConfirmCard, {
      props: {
        card: {
          token: 'act_cancel_race',
          label: '取消订单',
          status: 0
        }
      }
    });

    await fireEvent.click(view.getByRole('button', { name: '取消' }));

    await waitFor(() => {
      expect(view.getByText('操作处理中，请稍后再试')).toBeInTheDocument();
    });
    expect(view.getByText('待确认')).toBeInTheDocument();
    expect(view.getByRole('button', { name: '取消' })).toBeInTheDocument();
  });

  it('renders a server reconciliation state instead of leaving the card pending', async () => {
    vi.mocked(agentApi.confirmAction).mockResolvedValue({
      success: false,
      statusName: 'MANUAL_REVIEW',
      resultMessage: '自动核对已到边界，等待人工复核'
    });
    const view = render(AgentConfirmCard, {
      props: {
        card: {
          token: 'act_manual_review',
          label: '取消订单',
          status: 0
        }
      }
    });

    await fireEvent.click(view.getByRole('button', { name: '确认提交' }));

    await waitFor(() => {
      expect(view.getAllByText('自动核对已到边界，等待人工复核').length).toBeGreaterThan(0);
    });
    expect(view.queryByRole('button', { name: '确认提交' })).not.toBeInTheDocument();
  });
});
