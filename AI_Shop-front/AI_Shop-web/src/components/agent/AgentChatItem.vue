<template>
  <div v-if="showAi" class="bubble-row ai">
    <div class="ai-avatar-mini">
      <el-icon :size="16"><Service /></el-icon>
    </div>
    <div class="bubble ai-bubble" :class="{ 'is-wide': isWideBubble }">
      <div v-if="messageStatus === 0 && !hasRenderableContent" class="cancel-tip">已取消回复</div>
      <template v-else>
      <template v-if="productList?.length">
        <MarkdownContent
          v-if="productIntro"
          class="product-intro"
          :content="productIntro"
        />
        <p v-else class="biz-title">为您推荐以下商品</p>
        <AgentProductList :list="productList" @compare-products="(ids) => emit('compare-products', ids)" />
      </template>
      <template v-else-if="comparisonCard">
        <AgentProductComparison :card="comparisonCard" />
      </template>
      <template v-else-if="supportCaseCard">
        <AgentSupportCaseCard :card="supportCaseCard" />
      </template>
      <template v-else-if="isProductSearchEmpty">
        <p class="biz-title">商品搜索</p>
        <p class="empty-hint">未找到相关商品，请换个关键词试试，或让我为您推荐热销商品。</p>
      </template>
      <template v-else-if="orderList?.length">
        <p class="biz-title">为您查询到以下订单</p>
        <AgentOrderList :list="orderList" />
      </template>
      <template v-else-if="actionConfirmCard">
        <AgentConfirmCard :card="actionConfirmCard" @updated="onActionCardUpdated" />
      </template>
      <template v-else-if="orderSelectionCard">
        <AgentOrderSelectionCard
          :card="orderSelectionCard"
          :disabled="isStreaming"
          @select="(payload) => emit('select-order', payload)"
        />
      </template>
      <template v-else-if="isOrderSearchEmpty">
        <p class="biz-title">订单查询</p>
        <p class="empty-hint">未查询到相关订单，请核对订单号或稍后再试。</p>
      </template>
      <template v-else-if="isStreaming && streamText">
        <p class="stream-text">
          <span>{{ streamText }}</span>
          <span class="stream-cursor" aria-hidden="true" />
        </p>
      </template>
      <template v-else-if="isStreaming || waiting">
        <p class="typing">正在为您查询，请稍候…</p>
      </template>
      <template v-else-if="displayText">
        <MarkdownContent :content="displayText" :agent-rich="agentRich" />
      </template>
      <details v-if="sourceRefs.length" class="source-panel">
        <summary>参考来源（{{ sourceRefs.length }}）</summary>
        <ol>
          <li v-for="(sourceRef, index) in sourceRefs" :key="sourceKey(sourceRef, index)">
            <a
              v-if="sourceHref(sourceRef)"
              :href="sourceHref(sourceRef) || undefined"
              target="_blank"
              rel="noopener noreferrer"
            >
              {{ sourceLabel(sourceRef, index) }}
            </a>
            <span v-else>{{ sourceLabel(sourceRef, index) }}</span>
            <p v-if="sourceRef.snippet">{{ sourceRef.snippet }}</p>
            <small v-if="sourceMeta(sourceRef)">{{ sourceMeta(sourceRef) }}</small>
          </li>
        </ol>
      </details>
      <p v-if="messageStatus === 3 && hasRenderableContent" class="interrupt-tip">回复已中断，以上为已生成内容</p>
      </template>
      <div v-if="canFeedback" class="feedback-row" aria-label="回复反馈">
        <button
          type="button"
          :class="{ active: feedbackValue === 1 }"
          :disabled="feedbackSubmitting"
          @click="submitFeedback(1)"
        >
          有用
        </button>
        <button
          type="button"
          :class="{ active: feedbackValue === -1 }"
          :disabled="feedbackSubmitting"
          @click="submitFeedback(-1)"
        >
          需改进
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Service } from '@element-plus/icons-vue';
import MarkdownContent from '@/components/common/MarkdownContent.vue';
import AgentProductList from '@/components/agent/AgentProductList.vue';
import AgentProductComparison from '@/components/agent/AgentProductComparison.vue';
import AgentSupportCaseCard from '@/components/agent/AgentSupportCaseCard.vue';
import AgentOrderList from '@/components/agent/AgentOrderList.vue';
import AgentConfirmCard, { type ActionConfirmCardData } from '@/components/agent/AgentConfirmCard.vue';
import AgentOrderSelectionCard, {
  type OrderSelectionCardData
} from '@/components/agent/AgentOrderSelectionCard.vue';
import { agentApi } from '@/api/modules';
import { cleanAgentActionStreamText, containsAgentTable, stripEmbeddedProductJson } from '@/utils/agentMessageRender';
import { toast } from '@/utils/toast';
import { normalizeSourceRefs, type AgentSourceRef } from '@/utils/agentHistory';

const props = defineProps<{
  data: Record<string, any>;
  waiting?: boolean;
}>();

const emit = defineEmits<{
  'select-order': [payload: unknown];
  'compare-products': [productIds: string[]];
}>();

const messageStatus = computed(() => Number(props.data.status ?? 2));

const isStreaming = computed(() => messageStatus.value === 1);

const sourceRefs = computed(() => normalizeSourceRefs(props.data.sourceRefs));

const sourceLabel = (sourceRef: AgentSourceRef, index: number) => {
  const documentPath = [sourceRef.title, sourceRef.heading]
    .map((value) => String(value || '').trim())
    .filter((value, position, values) => value && values.indexOf(value) === position);
  if (documentPath.length) return documentPath.join(' · ');
  return sourceRef.question || sourceRef.source || `来源 ${index + 1}`;
};

const sourceHref = (sourceRef: AgentSourceRef) => {
  const url = String(sourceRef.url || '').trim();
  return /^https?:\/\//i.test(url) ? url : null;
};

const sourceMeta = (sourceRef: AgentSourceRef) => {
  const values = [
    sourceRef.source,
    sourceRef.version != null ? `版本 ${sourceRef.version}` : '',
    sourceRef.retrieval
  ].filter(Boolean);
  return [...new Set(values)].join(' · ');
};

const sourceKey = (sourceRef: AgentSourceRef, index: number) =>
  sourceRef.chunkId || `${sourceRef.type || 'source'}:${sourceRef.questionId || sourceRef.documentId || index}`;

const parseJsonList = (raw?: string | null) => {
  if (!raw || typeof raw !== 'string') return null;
  const text = raw.trim();
  if (!text.startsWith('[') && !text.startsWith('{')) return null;
  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) return parsed;
    if (parsed && Array.isArray(parsed.list)) return parsed.list;
    return null;
  } catch {
    return null;
  }
};

const isEmptyJsonList = (raw?: string | null) => {
  const parsed = parseJsonList(raw);
  if (parsed !== null) return parsed.length === 0;
  return typeof raw === 'string' && raw.trim() === '[]';
};

const isProductBiz = (bizType?: string | null) =>
  bizType === 'product_search' ||
  bizType === 'product_search.txt' ||
  bizType === 'BROWSE_RECOMMEND';

const isOrderBiz = (bizType?: string | null) =>
  bizType === 'query_order' || bizType === 'query_order.txt';

const looksLikeOrderCards = (list: unknown[] | null) => {
  if (!list?.length) return false;
  const first = list[0] as Record<string, unknown> | null;
  return !!(first && typeof first === 'object' && (first.orderId || first.order_id));
};

const parseJsonObject = (raw?: string | null) => {
  if (!raw || typeof raw !== 'string') return null;
  const text = raw.trim();
  if (!text.startsWith('{')) return null;
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
};

const actionConfirmCard = ref<ActionConfirmCardData | null>(null);

const orderSelectionCard = computed<OrderSelectionCardData | null>(() => {
  if (isStreaming.value) return null;
  const parsed = parseJsonObject(props.data.assistantMessage);
  if (
    parsed?.type !== 'ORDER_SELECTION'
    || !parsed.selectionId
    || !Array.isArray(parsed.candidates)
  ) return null;
  return parsed as unknown as OrderSelectionCardData;
});

const structuredCard = computed<Record<string, any> | null>(() => {
  if (isStreaming.value) return null;
  const parsed = parseJsonObject(props.data.assistantMessage);
  return parsed?.type ? parsed : null;
});

const comparisonCard = computed<Record<string, any> | null>(() => {
  const card = structuredCard.value;
  return card?.type === 'PRODUCT_COMPARISON' && Array.isArray(card.products) ? card : null;
});

const supportCaseCard = computed<Record<string, any> | null>(() => {
  const card = structuredCard.value;
  if (card?.type === 'SUPPORT_CASE_LIST' && Array.isArray(card.cases)) return card;
  if (card?.type === 'SUPPORT_CASE_DETAIL' && card.case && typeof card.case === 'object') return card;
  return null;
});

const syncActionConfirmCard = () => {
  if (isStreaming.value) {
    actionConfirmCard.value = null;
    return;
  }
  const parsed = parseJsonObject(props.data.assistantMessage);
  if (parsed?.type === 'ACTION_CONFIRM') {
    actionConfirmCard.value = {
      ...(parsed as ActionConfirmCardData),
      status: Number((parsed as ActionConfirmCardData).status ?? 0)
    };
    return;
  }
  actionConfirmCard.value = null;
};

watch(
  () => [props.data.assistantMessage, props.data.bizType, props.data.status, props.waiting] as const,
  () => syncActionConfirmCard(),
  { immediate: true }
);

const onActionCardUpdated = (card: ActionConfirmCardData) => {
  actionConfirmCard.value = card;
};

const productSearchPayload = computed(() => {
  if (isStreaming.value) return null;
  const raw = props.data.assistantMessage;
  if (!raw || typeof raw !== 'string') return null;
  const text = raw.trim();
  if (!text.startsWith('{')) return null;
  try {
    const parsed = JSON.parse(text);
    if (parsed?.type === 'PRODUCT_SEARCH_RESULT' && Array.isArray(parsed.products)) {
      return {
        intro: typeof parsed.intro === 'string' ? parsed.intro.trim() : '',
        products: parsed.products
      };
    }
  } catch {
    return null;
  }
  return null;
});

const productList = computed(() => {
  if (isStreaming.value) return null;
  const wrapped = productSearchPayload.value;
  const fromWrapped = (wrapped?.products || []).filter(
    (p: any) => p?.productId && (p?.productName || p?.product_name)
  );
  if (fromWrapped.length) return fromWrapped;
  const raw = props.data.assistantMessage;
  const parsed = parseJsonList(raw);
  if (!parsed?.length) return null;
  const usable = parsed.filter((p: any) => p?.productId && (p?.productName || p?.product_name));
  if (!usable.length) return null;
  if (isProductBiz(props.data.bizType)) return usable;
  if (usable[0]?.productId && usable[0]?.productName) return usable;
  return null;
});

const productIntro = computed(() =>
  stripEmbeddedProductJson(productSearchPayload.value?.intro || '')
);

const isProductSearchEmpty = computed(() => {
  if (isStreaming.value || props.waiting) return false;
  if (!isProductBiz(props.data.bizType)) return false;
  if (productSearchPayload.value) return !productSearchPayload.value.products.length;
  return isEmptyJsonList(props.data.assistantMessage);
});

const orderList = computed(() => {
  if (isStreaming.value) return null;
  const parsed = parseJsonList(props.data.assistantMessage);
  if (looksLikeOrderCards(parsed)) return parsed;
  if (!isOrderBiz(props.data.bizType)) return null;
  if (!parsed?.length) return null;
  return parsed;
});

const isOrderSearchEmpty = computed(() => {
  if (isStreaming.value || props.waiting) return false;
  if (!isOrderBiz(props.data.bizType)) return false;
  if (looksLikeOrderCards(parseJsonList(props.data.assistantMessage))) return false;
  return isEmptyJsonList(props.data.assistantMessage);
});

const streamText = computed(() => cleanAgentActionStreamText(props.data.assistantMessage));

const displayText = computed(() => {
  if (productList.value?.length || orderList.value?.length || comparisonCard.value || supportCaseCard.value) return '';
  if (actionConfirmCard.value) return '';
  if (orderSelectionCard.value) return '';
  if (isProductSearchEmpty.value || isOrderSearchEmpty.value) return '';
  const parsed = parseJsonObject(props.data.assistantMessage);
  if (parsed?.type === 'ACTION_CONFIRM') return '';
  if (parsed?.type === 'PRODUCT_SEARCH_RESULT') return '';
  if (parsed?.type === 'ORDER_SELECTION') return '';
  if (parsed?.type === 'PRODUCT_COMPARISON' || parsed?.type === 'SUPPORT_CASE_LIST' || parsed?.type === 'SUPPORT_CASE_DETAIL') return '';
  let text = (props.data.assistantMessage || '').trim();
  if (text === '[]') return '';
  // Hide bare product-id JSON arrays the model sometimes dumps.
  const asList = parseJsonList(text);
  if (
    asList?.length &&
    asList.every((p: any) => p?.productId && !(p?.productName || p?.product_name))
  ) {
    return '';
  }
  text = stripEmbeddedProductJson(text);
  return text;
});

const agentRich = computed(
  () =>
    props.data.bizType === 'query_logistics' ||
    props.data.bizType === 'product_consult' ||
    containsAgentTable(displayText.value)
);

const showAi = computed(() => {
  if (messageStatus.value === 0) return hasRenderableContent.value;
  if (props.waiting) return true;
  if (isStreaming.value) return true;
  if (messageStatus.value === 3) return hasRenderableContent.value;
  if (productList.value?.length || orderList.value?.length || comparisonCard.value || supportCaseCard.value) return true;
  if (actionConfirmCard.value) return true;
  if (orderSelectionCard.value) return true;
  if (isProductSearchEmpty.value || isOrderSearchEmpty.value) return true;
  if (displayText.value) return true;
  return false;
});

const hasRenderableContent = computed(
  () =>
    !!(productList.value?.length ||
      orderList.value?.length ||
      comparisonCard.value ||
      supportCaseCard.value ||
      actionConfirmCard.value ||
      orderSelectionCard.value ||
      isProductSearchEmpty.value ||
      isOrderSearchEmpty.value ||
      displayText.value ||
      sourceRefs.value.length ||
      props.waiting ||
      (isStreaming.value && streamText.value))
);

const isWideBubble = computed(
  () =>
    !!(
      productList.value?.length ||
      orderList.value?.length ||
      comparisonCard.value ||
      supportCaseCard.value ||
      actionConfirmCard.value ||
      orderSelectionCard.value ||
      isProductSearchEmpty.value ||
      isOrderSearchEmpty.value ||
      agentRich.value
      || sourceRefs.value.length
    )
);

const feedbackByMessage = ref<Record<string, 1 | -1>>({});
const feedbackSubmitting = ref(false);
const feedbackMessageKey = computed(() => String(props.data.messageId || ''));
const feedbackValue = computed(() =>
  feedbackMessageKey.value ? feedbackByMessage.value[feedbackMessageKey.value] : undefined
);
const canFeedback = computed(
  () =>
    messageStatus.value === 2 &&
    !!feedbackMessageKey.value &&
    hasRenderableContent.value &&
    !isStreaming.value
);

const submitFeedback = async (rating: 1 | -1) => {
  const id = Number(props.data.messageId);
  if (!id || feedbackSubmitting.value) return;
  feedbackSubmitting.value = true;
  try {
    await agentApi.feedback(
      id,
      rating,
      rating > 0 ? 'HELPFUL' : 'NEEDS_IMPROVEMENT'
    );
    feedbackByMessage.value[feedbackMessageKey.value] = rating;
    toast.success(rating > 0 ? '感谢反馈' : '已记录，会用于优化回复');
  } catch {
    toast.error('反馈提交失败，请稍后重试');
  } finally {
    feedbackSubmitting.value = false;
  }
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.bubble-row.ai {
  display: flex;
  justify-content: flex-start;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  box-sizing: border-box;
  margin-bottom: 8px;
}

.bubble {
  width: fit-content;
  max-width: min(75%, 520px);
  padding: 9px 12px;
  font-size: 13px;
  line-height: 1.45;
  border-radius: 8px;
  word-break: break-word;
  flex: 0 1 auto;
  min-width: 0;
}

.ai-avatar-mini {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 50%;
  background: $color-card;
  color: $color-primary;
  display: grid;
  place-items: center;
  box-shadow: $shadow-xs;
}

.ai-bubble {
  background: $color-card;
  color: $color-text-body;
  border-bottom-left-radius: 4px;
  box-shadow: $shadow-xs;

  &.is-wide {
    width: auto;
    max-width: calc(100% - 36px);
    flex: 1 1 auto;
    min-width: 0;
  }
}

.biz-title {
  margin: 0 0 8px;
  font-size: 13px;
  color: $color-text-title;
}

.typing,
.cancel-tip,
.interrupt-tip,
.empty-hint {
  margin: 0;
  font-size: 13px;
  color: $color-text-muted;
}

.interrupt-tip {
  margin-top: 8px;
  font-size: 12px;
  color: $color-text-muted;
}

.empty-hint {
  line-height: 1.55;
}

.product-intro {
  margin: 0 0 8px;
}

.source-panel {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid $color-border-gray;
  color: $color-text-muted;
  font-size: 12px;

  summary {
    width: fit-content;
    cursor: pointer;
    color: $color-text-body;
  }

  ol {
    margin: 8px 0 0;
    padding-left: 18px;
  }

  li + li {
    margin-top: 8px;
  }

  a {
    color: $color-primary;
  }

  p {
    margin: 3px 0;
    line-height: 1.45;
  }

  small {
    color: $color-text-muted;
  }
}

.stream-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: $color-text-body;
  white-space: pre-wrap;
  word-break: break-word;
}

.stream-cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: $color-primary;
  animation: blink 0.9s step-end infinite;
}

@keyframes blink {
  50% {
    opacity: 0;
  }
}

:deep(.markdown-content) {
  font-size: 13px;
  line-height: 1.45;
  max-width: 100%;

  p {
    margin: 0;

    & + p {
      margin-top: 6px;
    }
  }
}

.ai-bubble:not(.is-wide) :deep(.markdown-content) {
  width: fit-content;
}

:deep(.agent-orders) {
  font-size: 12px;
}

.feedback-row {
  display: flex;
  gap: 8px;
  margin-top: 10px;

  button {
    height: 26px;
    padding: 0 10px;
    border: 1px solid $color-border-gray;
    border-radius: $radius-pill;
    background: #fff;
    color: $color-text-muted;
    font-size: 12px;
    cursor: pointer;

    &.active {
      color: $color-primary;
      border-color: rgba($color-primary, 0.36);
      background: $color-primary-soft;
    }

    &:disabled {
      cursor: not-allowed;
      opacity: 0.6;
    }
  }
}
</style>
