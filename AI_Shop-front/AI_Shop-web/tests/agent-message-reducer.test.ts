import { describe, expect, it } from 'vitest';
import {
  upsertAgentHttpMessage,
  upsertAgentStreamMessage,
  type AgentHistoryMessage
} from '@/utils/agentHistory';

describe('agent message reducer', () => {
  it('keeps a terminal websocket message when HTTP send response arrives later', () => {
    const list: AgentHistoryMessage[] = [];
    upsertAgentStreamMessage(list, {
      messageId: '11',
      assistantMessage: '人工回复',
      messageType: 'support',
      outPutType: 1
    });
    upsertAgentHttpMessage(list, {
      messageId: 11,
      userMessage: '在吗',
      assistantMessage: '',
      status: 1
    });

    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({
      messageId: 11,
      userMessage: '在吗',
      assistantMessage: '人工回复',
      status: 2
    });
  });

  it('upserts an unknown human support message without touching another stream', () => {
    const list: AgentHistoryMessage[] = [
      { messageId: 20, assistantMessage: '正在', status: 1 }
    ];
    const result = upsertAgentStreamMessage(list, {
      messageId: 21,
      assistantMessage: '您好，我来处理',
      messageType: 'support',
      outPutType: 1
    });

    expect(result?.created).toBe(true);
    expect(list).toHaveLength(2);
    expect(list[0]).toMatchObject({ messageId: 20, assistantMessage: '正在', status: 1 });
    expect(list[1]).toMatchObject({ messageId: 21, assistantMessage: '您好，我来处理', status: 2 });
  });

  it('does not append a delayed partial after the terminal frame', () => {
    const list: AgentHistoryMessage[] = [];
    upsertAgentStreamMessage(list, {
      messageId: 30,
      assistantMessage: '最终答案',
      sourceRefs: [{ heading: '退货规则', version: 2 }],
      outPutType: 1
    });
    upsertAgentStreamMessage(list, {
      messageId: 30,
      assistantMessage: '迟到片段',
      outPutType: 0
    });
    upsertAgentStreamMessage(list, {
      messageId: 30,
      assistantMessage: '最终答案',
      outPutType: 1
    });

    expect(list).toHaveLength(1);
    expect(list[0].assistantMessage).toBe('最终答案');
    expect(list[0].sourceRefs).toEqual([{ heading: '退货规则', version: 2 }]);
  });

  it('creates an unknown partial and appends subsequent chunks by message id', () => {
    const list: AgentHistoryMessage[] = [];
    upsertAgentStreamMessage(list, { messageId: 40, assistantMessage: '第', outPutType: 0 });
    upsertAgentStreamMessage(list, { messageId: 40, assistantMessage: '一段', outPutType: 0 });

    expect(list).toEqual([{ messageId: 40, assistantMessage: '第一段', status: 1 }]);
  });

  it('keeps one order-selection card when HTTP and DONE arrive out of order', () => {
    const list: AgentHistoryMessage[] = [];
    const card = JSON.stringify({
      type: 'ORDER_SELECTION',
      selectionId: 'sel_1',
      candidates: [{ targetType: 'ORDER', targetId: 'o1', orderId: 'o1' }]
    });
    upsertAgentStreamMessage(list, {
      messageId: '50',
      assistantMessage: card,
      bizType: 'order_selection',
      outPutType: 1
    });
    upsertAgentHttpMessage(list, {
      messageId: 50,
      userMessage: '没发货的耳机我要退款',
      assistantMessage: '',
      status: 1
    });
    upsertAgentStreamMessage(list, {
      messageId: 50,
      assistantMessage: card,
      bizType: 'order_selection',
      outPutType: 1
    });

    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({
      messageId: 50,
      assistantMessage: card,
      bizType: 'order_selection',
      status: 2
    });
  });
});
