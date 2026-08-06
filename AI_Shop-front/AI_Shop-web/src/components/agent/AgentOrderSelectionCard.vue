<template>
  <section class="order-selection" aria-label="请选择订单">
    <p class="selection-prompt">{{ card.prompt }}</p>
    <div class="candidate-list">
      <article
        v-for="candidate in card.candidates"
        :key="`${candidate.targetType}:${candidate.targetId}`"
        class="candidate-row"
      >
        <ProductImage
          v-if="candidate.cover"
          class="candidate-cover"
          :source="candidate.cover"
          width="52"
          height="52"
        />
        <div class="candidate-main">
          <p class="candidate-name">{{ candidate.productName || '订单商品' }}</p>
          <p v-if="candidate.propertyInfo" class="candidate-sku">{{ candidate.propertyInfo }}</p>
          <p class="candidate-meta">
            <span>{{ candidate.orderStatusName || '订单' }}</span>
            <span v-if="candidate.orderTime">{{ displayTime(candidate.orderTime) }}</span>
            <span v-if="candidate.amount != null">¥{{ formatAmount(candidate.amount) }}</span>
          </p>
          <p class="candidate-order" :title="candidate.orderId">订单 {{ candidate.orderId }}</p>
        </div>
        <button
          type="button"
          class="select-button"
          :disabled="disabled || expired || submittingTarget !== null || selectedTarget !== null"
          @click="selectCandidate(candidate)"
        >
          <el-icon v-if="selectedTarget === candidate.targetId"><Check /></el-icon>
          <span>{{ selectedTarget === candidate.targetId ? '已选择' : actionLabel }}</span>
        </button>
      </article>
    </div>
    <p v-if="expired" class="selection-expired">候选已过期，请重新描述要办理的订单。</p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { Check } from '@element-plus/icons-vue';
import ProductImage from '@/components/common/ProductImage.vue';

export interface OrderSelectionCandidate {
  targetType: 'ORDER' | 'ORDER_ITEM';
  targetId: string;
  orderId: string;
  orderItemId?: string | null;
  productId?: string | null;
  productName?: string | null;
  propertyInfo?: string | null;
  cover?: string | null;
  amount?: number | null;
  orderStatus?: number | null;
  orderStatusName?: string | null;
  orderTime?: string | null;
}

export interface OrderSelectionCardData {
  type: 'ORDER_SELECTION';
  selectionId: string;
  sourceMessageId: string;
  intent: string;
  prompt: string;
  expiresAt: string;
  candidates: OrderSelectionCandidate[];
}

const props = defineProps<{
  card: OrderSelectionCardData;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  select: [payload: {
    card: OrderSelectionCardData;
    candidate: OrderSelectionCandidate;
    done: (success: boolean) => void;
  }];
}>();

const submittingTarget = ref<string | null>(null);
const selectedTarget = ref<string | null>(null);
const expired = computed(() => {
  const value = Date.parse(props.card.expiresAt);
  return Number.isFinite(value) && value <= Date.now();
});

const actionLabel = computed(() => ({
  REFUND: '选择退款',
  REFUND_STATUS: '查退款',
  QUERY_LOGISTICS: '查物流',
  QUERY_FULFILLMENT: '查发货',
  CANCEL_ORDER: '选择取消',
  CONFIRM_RECEIPT: '确认此单',
  PRODUCT_REVIEW: '评价此单',
  RECOMMENT: '追评此单',
  QUERY_COMMENT: '看评价',
  QUERY_ORDER: '查看此单',
  ADDRESS_CHANGE: '处理地址',
  INVOICE: '处理发票',
  DAMAGED_OR_WRONG_ITEM: '处理此单',
  AFTERSALES_UNKNOWN: '处理此单'
}[props.card.intent] || '选择'));

const selectCandidate = (candidate: OrderSelectionCandidate) => {
  if (props.disabled || expired.value || submittingTarget.value || selectedTarget.value) return;
  submittingTarget.value = candidate.targetId;
  emit('select', {
    card: props.card,
    candidate,
    done: (success: boolean) => {
      submittingTarget.value = null;
      if (success) selectedTarget.value = candidate.targetId;
    }
  });
};

const displayTime = (value: string) => value.replace('T', ' ').slice(0, 16);
const formatAmount = (value: number) => Number(value).toFixed(2);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.order-selection {
  width: min(520px, 72vw);
  max-width: 100%;
}

.selection-prompt {
  margin: 0 0 10px;
  color: $color-text-title;
  font-size: 13px;
  line-height: 1.55;
}

.candidate-list {
  border-top: 1px solid $color-border;
}

.candidate-row {
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid $color-border;
}

.candidate-cover {
  border-radius: $radius-xs;
  overflow: hidden;
}

.candidate-main {
  min-width: 0;
}

.candidate-name,
.candidate-sku,
.candidate-meta,
.candidate-order {
  margin: 0;
}

.candidate-name {
  color: $color-text-title;
  font-size: 13px;
  line-height: 1.4;
}

.candidate-sku,
.candidate-order {
  margin-top: 3px;
  color: $color-text-muted;
  font-size: 10px;
  line-height: 1.35;
}

.candidate-order {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.candidate-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  margin-top: 5px;
  color: $color-text-body;
  font-size: 11px;
}

.select-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-width: 82px;
  min-height: 34px;
  padding: 0 10px;
  border: 1px solid $color-primary;
  border-radius: $radius-xs;
  background: $color-primary;
  color: #fff;
  font-size: 12px;
  cursor: pointer;

  &:disabled {
    cursor: not-allowed;
    opacity: 0.55;
  }
}

.selection-expired {
  margin: 8px 0 0;
  color: $color-text-muted;
  font-size: 11px;
}

@media (max-width: 520px) {
  .order-selection {
    width: min(100%, 82vw);
  }

  .candidate-row {
    grid-template-columns: 48px minmax(0, 1fr);
  }

  .candidate-cover {
    width: 48px;
    height: 48px;
  }

  .select-button {
    grid-column: 2;
    justify-self: start;
  }
}
</style>
