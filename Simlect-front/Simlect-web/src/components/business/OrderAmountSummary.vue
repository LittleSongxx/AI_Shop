<template>
  <div class="order-amount-summary" :class="{ compact }">
    <div v-if="showOriginal" class="amount-row">
      <span class="label">{{ originalLabel }}</span>
      <span class="value">¥{{ formatOrderMoney(order?.originalAmount) }}</span>
    </div>
    <div v-if="showCoupon" class="amount-row discount">
      <span class="label">{{ couponLabel }}</span>
      <span class="value">-¥{{ formatOrderMoney(order?.couponDiscountAmount) }}</span>
    </div>
    <div class="amount-row pay">
      <span class="label">{{ payLabel }}</span>
      <strong class="value pay-value">¥{{ formatOrderMoney(order?.amount) }}</strong>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import {
  formatOrderMoney,
  hasOrderCouponDiscount,
  orderCouponSummaryText
} from '@/utils/orderAmount';

const props = withDefaults(
  defineProps<{
    order?: Record<string, any> | null;
    compact?: boolean;
    originalLabel?: string;
    payLabel?: string;
  }>(),
  {
    order: null,
    compact: false,
    originalLabel: '商品总价',
    payLabel: '实付款'
  }
);

const showCoupon = computed(() => hasOrderCouponDiscount(props.order));

const showOriginal = computed(() => showCoupon.value);

const couponLabel = computed(() => {
  const summary = orderCouponSummaryText(props.order);
  return summary ? `优惠券：${summary}` : '优惠券';
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.order-amount-summary {
  display: flex;
  flex-direction: column;
  gap: 8px;

  &.compact {
    gap: 4px;
    align-items: flex-end;
    text-align: right;
  }
}

.amount-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: $color-text-body;

  .label {
    flex: 1;
    min-width: 0;
    color: $color-text-muted;
    word-break: break-all;
  }

  .value {
    flex-shrink: 0;
    color: $color-text-title;
  }

  &.discount .value {
    color: $color-price;
  }

  &.pay {
    margin-top: 2px;
    font-size: 14px;

    .pay-value {
      font-size: inherit;
      font-weight: 700;
      color: $color-price;
    }
  }
}

.order-amount-summary.compact .amount-row {
  justify-content: flex-end;

  .label {
    flex: unset;
  }
}
</style>
