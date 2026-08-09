<template>
  <main
    ref="listRef"
    class="chat-scroll"
    :class="{ 'is-embedded-composer': composerEmbedded }"
    @scroll="onListScroll"
  >
    <div v-if="messageList.length" class="history-toolbar">
      <el-tooltip content="清除当前会话记录" placement="bottom">
        <button
          type="button"
          class="history-clear"
          aria-label="清除会话记录"
          :disabled="answering || clearingHistory"
          @click="clearVisibleHistory"
        >
          <el-icon :size="17"><Delete /></el-icon>
        </button>
      </el-tooltip>
    </div>
    <div class="chat-scroll-inner">
      <p v-if="loadingHistory && !messageList.length" class="history-loading">历史消息加载中…</p>
      <button
        v-else-if="historyLoadFailed && !messageList.length"
        type="button"
        class="history-error"
        @click="reloadHistory"
      >
        历史消息加载失败，点击重试
      </button>
      <p v-else-if="!messageList.length && !loadingHistory" class="welcome">
        您好，我是智选智能客服小智，可以帮您查商品、查订单、推荐精选。
      </p>
      <p
        v-if="messageList.length && pageNo >= pageTotal && !loadingHistory"
        class="history-top-tip"
      >
        没有更多历史消息了
      </p>
      <p v-else-if="loadingHistory && messageList.length" class="history-top-tip">加载更早的消息…</p>
      <div v-for="(item, index) in messageList" :key="item.messageId || `msg-${index}`" class="msg-group">
        <AgentUserBubble v-if="item.userMessage" :user-message="item.userMessage" />
        <AgentChatItem
          v-if="shouldShowAi(item)"
          :data="item"
          :waiting="Number(item.status) === 1 && streamWaiting && item === currentMessage"
          @select-order="onSelectOrder"
          @compare-products="onCompareProducts"
        />
      </div>
    </div>
  </main>
</template>

<script setup lang="ts">
import { Delete } from '@element-plus/icons-vue';
import { ElMessageBox } from 'element-plus';
import { inject, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { agentApi } from '@/api/modules';
import { agentComposerEmbeddedKey } from '@/composables/agentEmbed';
import AgentChatItem from '@/components/agent/AgentChatItem.vue';
import AgentUserBubble from '@/components/agent/AgentUserBubble.vue';
import { useAgentMessageStore } from '@/stores/agentMessage';
import { useAuthStore } from '@/stores/auth';
import { mitter } from '@/utils/eventBus';
import { toast } from '@/utils/toast';
import {
  extractHistoryPage,
  mergeHistoryMessages,
  normalizeAgentHistoryMessage,
  sortHistoryMessages,
  upsertAgentHttpMessage,
  upsertAgentStreamMessage,
  type AgentHistoryMessage
} from '@/utils/agentHistory';

const composerEmbedded = inject(agentComposerEmbeddedKey, false);
const agentMessageStore = useAgentMessageStore();
const listRef = ref<HTMLElement>();
const loadingHistory = ref(false);
const historyLoadFailed = ref(false);
const clearingHistory = ref(false);
const answering = ref(false);
const streamWaiting = ref(false);
const currentMessage = ref<AgentHistoryMessage | null>(null);

const messageList = ref<AgentHistoryMessage[]>([]);
const pageNo = ref(0);
const pageTotal = ref(0);

let maxMessageId: number | null = null;
let oldScrollHeight = 0;
let initialLoadDone = false;

const shouldShowAi = (item: AgentHistoryMessage) => {
  const status = Number(item.status);
  if (status === 0 || status === 1 || status === 3) return true;
  return !!(item.assistantMessage || '').trim();
};

const stickToBottom = ref(true);

const isNearBottom = (el: HTMLElement, threshold = 80) =>
  el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;

const onListScroll = () => {
  const el = listRef.value;
  if (!el) return;
  stickToBottom.value = isNearBottom(el);
  if (!initialLoadDone || loadingHistory.value) return;
  if (el.scrollTop > 8) return;
  if (pageNo.value >= pageTotal.value) return;
  oldScrollHeight = el.scrollHeight;
  void loadHistoryMessage();
};

const finishAnswering = (message: AgentHistoryMessage) => {
  if (
    !currentMessage.value ||
    String(currentMessage.value.messageId) !== String(message.messageId)
  ) {
    return;
  }
  streamWaiting.value = false;
  answering.value = false;
  mitter.emit('answering', false);
  currentMessage.value = null;
  void scrollBottom(true);
};

const scrollBottom = async (force = false) => {
  await nextTick();
  const el = listRef.value;
  if (!el) return;
  if (!force && !stickToBottom.value) return;

  const apply = () => {
    el.scrollTop = Math.max(0, el.scrollHeight - el.clientHeight);
    stickToBottom.value = isNearBottom(el);
  };

  apply();
  requestAnimationFrame(apply);
};

const scrollToLatestOnOpen = async () => {
  stickToBottom.value = true;
  await scrollBottom(true);
  window.setTimeout(() => void scrollBottom(true), 120);
  window.setTimeout(() => void scrollBottom(true), 320);
};

const restoreScrollAfterPrepend = async () => {
  await nextTick();
  const el = listRef.value;
  if (!el) return;
  el.scrollTop = Math.max(0, el.scrollHeight - oldScrollHeight);
};

watch(
  () => agentMessageStore.message,
  (newVal) => {
    if (!newVal) return;
    const result = upsertAgentStreamMessage(messageList.value, newVal);
    if (!result) return;
    if (result.terminal) {
      finishAnswering(result.message);
      if (result.created) void scrollBottom();
      return;
    }
    if (
      currentMessage.value &&
      String(currentMessage.value.messageId) === String(result.message.messageId)
    ) {
      streamWaiting.value = false;
    }
    void scrollBottom();
  },
  { deep: true }
);

const onSendMessage = (payload?: unknown) => {
  const message = payload as Record<string, any>;
  if (!message?.messageId) return;

  const target = upsertAgentHttpMessage(messageList.value, message);
  if (!target) return;
  currentMessage.value = target.status === 1 ? target : null;

  answering.value = target.status === 1;
  streamWaiting.value = target.status === 1;
  stickToBottom.value = true;
  if (target.status === 1) {
    mitter.emit('answering', { answering: true, messageId: message.messageId });
  }
  void scrollBottom(true);
};

const onSelectOrder = async (payload: unknown) => {
  const data = payload as {
    card?: { selectionId?: string };
    candidate?: { targetType?: 'ORDER' | 'ORDER_ITEM'; targetId?: string };
    done?: (success: boolean) => void;
  };
  const selectionId = data.card?.selectionId;
  const targetType = data.candidate?.targetType;
  const targetId = data.candidate?.targetId;
  if (!selectionId || !targetType || !targetId) {
    data.done?.(false);
    toast.error('订单候选参数无效');
    return;
  }
  if (answering.value) {
    data.done?.(false);
    toast.info('请等待当前回复完成');
    return;
  }
  try {
    const message = await agentApi.selectOrderCandidate(selectionId, targetType, targetId);
    if (!message?.messageId) throw new Error('订单候选处理失败');
    data.done?.(true);
    mitter.emit('sendMessage', { ...message });
  } catch (error: any) {
    data.done?.(false);
    toast.error(error?.info || error?.message || '订单候选处理失败，请重试');
  }
};

const onCompareProducts = (productIds: string[]) => {
  const ids = [...new Set((productIds || []).map((id) => String(id).trim()).filter(Boolean))];
  if (ids.length < 2 || ids.length > 4) {
    toast.error('请选择 2 到 4 个商品进行比较');
    return;
  }
  mitter.emit('compareProducts', { productIds: ids });
};

const onCancelMessage = async (payload?: unknown) => {
  const data = payload as { messageId?: number };
  const id = data?.messageId;
  let target = currentMessage.value;
  if (!target && id != null) {
    target = messageList.value.find((m) => String(m.messageId) === String(id)) ?? null;
  }
  if (!target || id == null) return;

  const partial = (target.assistantMessage || '').trim();
  if (partial) {
    target.status = 3;
  } else {
    target.status = 0;
    target.assistantMessage = '';
    target.bizType = undefined;
  }
  answering.value = false;
  streamWaiting.value = false;
  mitter.emit('answering', false);
  if (currentMessage.value === target) currentMessage.value = null;

  try {
    await agentApi.cancelMessage(id, partial || undefined);
  } catch {
    toast.error('停止失败，请重试');
  }
};

const loadHistoryMessage = async () => {
  if (loadingHistory.value) return;

  const nextPage = pageNo.value + 1;
  if (pageTotal.value > 0 && nextPage > pageTotal.value) return;

  loadingHistory.value = true;
  historyLoadFailed.value = false;

  try {
    const res = await agentApi.loadHistoryMessage({
      pageNo: nextPage,
      ...(maxMessageId != null ? { maxMessageId } : {})
    });
    const page = extractHistoryPage(res);
    const rawList = page.list as Record<string, unknown>[];

    if (nextPage === 1 && rawList.length > 0) {
      maxMessageId = Number(rawList[0]?.messageId) || null;
    }

    const incoming = sortHistoryMessages(
      rawList.map((row) => normalizeAgentHistoryMessage(row))
    );

    messageList.value = mergeHistoryMessages(incoming, messageList.value);

    pageNo.value = page.pageNo || nextPage;
    pageTotal.value = page.pageTotal || 1;

    const last = messageList.value[messageList.value.length - 1];
    if (last && Number(last.status) === 1) {
      currentMessage.value = last;
      answering.value = true;
      streamWaiting.value = true;
      mitter.emit('answering', { answering: true, messageId: last.messageId });
    }

    if (nextPage === 1) {
      initialLoadDone = true;
      await scrollToLatestOnOpen();
    } else {
      await restoreScrollAfterPrepend();
    }
  } catch (err) {
    console.error('加载历史消息失败', err);
    historyLoadFailed.value = true;
  } finally {
    loadingHistory.value = false;
  }
};

const reloadHistory = () => {
  messageList.value = [];
  pageNo.value = 0;
  pageTotal.value = 0;
  maxMessageId = null;
  initialLoadDone = false;
  void loadHistoryMessage();
};

const clearVisibleHistory = async () => {
  if (answering.value || clearingHistory.value || !messageList.value.length) return;
  try {
    await ElMessageBox.confirm(
      '仅清除当前会话中的聊天记录；购物偏好、长期记忆和待确认操作都会保留。',
      '清除会话记录',
      {
        confirmButtonText: '确认清除',
        cancelButtonText: '取消',
        type: 'warning'
      }
    );
  } catch {
    return;
  }

  clearingHistory.value = true;
  try {
    await agentApi.clearHistoryMessage();
    messageList.value = [];
    pageNo.value = 0;
    pageTotal.value = 0;
    maxMessageId = null;
    initialLoadDone = true;
    currentMessage.value = null;
    toast.success('会话记录已清除，已有记忆仍会保留');
  } catch (error) {
    console.error('清除会话记录失败', error);
    toast.error('清除失败，请稍后重试');
  } finally {
    clearingHistory.value = false;
  }
};

const onComposerReady = () => {
  if (stickToBottom.value) void scrollBottom(true);
};

onMounted(async () => {
  const authed = await useAuthStore().ensureSession();
  if (!authed) {
    historyLoadFailed.value = true;
    return;
  }

  mitter.on('sendMessage', onSendMessage as (p?: unknown) => void);
  mitter.on('cancelMessage', onCancelMessage);
  mitter.on('agentComposerReady', onComposerReady);
  await loadHistoryMessage();
});

onUnmounted(() => {
  mitter.off('sendMessage', onSendMessage as (p?: unknown) => void);
  mitter.off('cancelMessage', onCancelMessage);
  mitter.off('agentComposerReady', onComposerReady);
  currentMessage.value = null;
  answering.value = false;
  maxMessageId = null;
  initialLoadDone = false;
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.chat-scroll {
  flex: 1 1 0;
  width: 100%;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-bottom: calc(var(--agent-composer-inset, 148px) + 12px);
  background: transparent;
  -webkit-overflow-scrolling: touch;
  touch-action: pan-y;
  position: relative;
  z-index: 1;

  &.is-embedded-composer {
    padding-bottom: 12px;
  }
}

.chat-scroll-inner {
  width: 100%;
  min-height: 100%;
  padding: 12px 14px;
  box-sizing: border-box;
}

.history-toolbar {
  position: sticky;
  top: 0;
  z-index: 3;
  display: flex;
  justify-content: flex-end;
  height: 40px;
  padding: 6px 12px 0;
  box-sizing: border-box;
  background: rgba(250, 250, 250, 0.96);
}

.history-clear {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 1px solid $color-border-gray;
  border-radius: $radius-sm;
  background: $color-card;
  color: $color-text-muted;
  cursor: pointer;

  &:hover:not(:disabled) {
    border-color: $color-primary;
    color: $color-primary;
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }
}

.msg-group {
  width: 100%;
  margin-bottom: 14px;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
}

:deep(.bubble-row.user) {
  justify-content: flex-end;

  .bubble,
  .consult-product-card.is-bubble {
    margin-left: auto;
  }
}

:deep(.bubble-row.ai) {
  justify-content: flex-start;
  align-items: flex-start;
}

.welcome,
.history-loading,
.history-error,
.history-top-tip {
  margin: 16px 0;
  text-align: center;
  font-size: 13px;
  color: $color-text-muted;
  line-height: 1.6;
}

.history-error {
  color: $color-primary;
  cursor: pointer;
  border: none;
  background: none;
  font: inherit;
  width: 100%;
  padding: 0;
}

.history-top-tip {
  margin: 8px 0 12px;
  font-size: 12px;
}
</style>
