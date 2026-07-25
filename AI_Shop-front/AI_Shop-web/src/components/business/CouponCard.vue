<template>
  <article class="discount-card" :class="{ 'is-disabled': !canReceive, 'is-purchased': hasBought }">
    <div class="card-left">
      <div class="price-wrap">
        <span v-if="leftDisplay.prefix" class="currency">{{ leftDisplay.prefix }}</span>
        <span class="price">{{ leftDisplay.value }}</span>
        <span v-if="leftDisplay.suffix" class="suffix">{{ leftDisplay.suffix }}</span>
      </div>
      <p class="threshold">{{ thresholdText }}</p>
    </div>

    <div class="card-right">
      <div class="card-top">
        <h4 class="name">{{ coupon.couponName || '优惠券' }}</h4>
        <span class="type-tag" :class="`type-${coupon.couponType}`">{{ typeLabel }}</span>
      </div>

      <div class="card-middle">
        <p class="meta-row">
          <span class="label">有效期</span>
          <span class="value">{{ validEndText }}</span>
        </p>
        <p v-if="coupon.rushingStartTime && coupon.rushingEndTime" class="meta-row rush">
          <span class="label">秒杀</span>
          <span class="value">{{ rushRangeText }}</span>
        </p>
      </div>

      <div class="card-bottom">
        <div class="stock-block">
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: `${progress}%` }" />
          </div>
          <span class="stock-text">已抢 {{ sold }} / {{ stockTotalLabel }}</span>
        </div>
        <el-button
          v-if="type === 'available'"
          class="buy-btn"
          :class="{ 'is-purchased': hasBought }"
          type="primary"
          round
          :plain="hasBought"
          :disabled="!canReceive || receiving"
          :loading="receiving"
          @click="$emit('receive', coupon)"
        >
          {{ btnText }}
        </el-button>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import {
  canReceiveCoupon,
  couponLeftMainDisplay,
  couponReceiveBtnText,
  couponSoldCount,
  couponSoldProgress,
  couponStockTotalLabel,
  couponThresholdText,
  couponTypeLabel,
  formatCouponDateTime,
  resolveCouponHasBought
} from '@/utils/couponPlaza';

const props = withDefaults(
  defineProps<{ coupon: Record<string, any>; type: 'user' | 'available'; receiving?: boolean }>(),
  { receiving: false }
);
defineEmits<{ receive: [Record<string, any>] }>();

const leftDisplay = computed(() => couponLeftMainDisplay(props.coupon));
const thresholdText = computed(() => couponThresholdText(props.coupon));
const typeLabel = computed(() => couponTypeLabel(props.coupon.couponType));
const progress = computed(() => couponSoldProgress(props.coupon));
const sold = computed(() => couponSoldCount(props.coupon));
const stockTotalLabel = computed(() => couponStockTotalLabel(props.coupon));
const hasBought = computed(() => resolveCouponHasBought(props.coupon));
const canReceive = computed(() => (props.type === 'available' ? canReceiveCoupon(props.coupon) : false));
const btnText = computed(() => couponReceiveBtnText(props.coupon));
const validEndText = computed(() =>
  props.coupon.validEndTime ? `至 ${formatCouponDateTime(props.coupon.validEndTime)}` : '长期有效'
);
const rushRangeText = computed(() =>
  `${formatCouponDateTime(props.coupon.rushingStartTime)} ~ ${formatCouponDateTime(props.coupon.rushingEndTime)}`
);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.discount-card {
  display: flex;
  background: #fff;
  border-radius: 12px;
  margin-bottom: 12px;
  overflow: hidden;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);

  &.is-disabled {
    opacity: 0.88;
  }
}

.card-left {
  width: 108px;
  flex-shrink: 0;
  background: $color-accent-gradient-gold;
  color: #fff;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 14px 8px;
  position: relative;

  &::after {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    right: -6px;
    width: 12px;
    background: repeating-linear-gradient(
      to bottom,
      $color-bg 0,
      $color-bg 4px,
      transparent 4px,
      transparent 8px
    );
  }

  .price-wrap {
    display: flex;
    align-items: baseline;
    line-height: 1;

    .currency {
      font-size: 14px;
      font-weight: 500;
    }

    .price {
      font-size: 32px;
      font-weight: 700;
    }

    .suffix {
      font-size: 14px;
      font-weight: 600;
      margin-left: 2px;
    }
  }

  .threshold {
    margin: 6px 0 0;
    font-size: 11px;
    opacity: 0.9;
    text-align: center;
  }
}

.card-right {
  flex: 1;
  min-width: 0;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-top {
  display: flex;
  align-items: center;
  gap: 8px;

  .name {
    flex: 1;
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    color: $color-text-title;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .type-tag {
    flex-shrink: 0;
    font-size: 10px;
    padding: 2px 6px;
    border-radius: $radius-xs;

    &.type-1,
    &.type-2,
    &.type-3 {
      background: $color-primary-soft;
      color: $color-primary;
    }
  }
}

.meta-row {
  margin: 0;
  font-size: 12px;
  color: $color-text-muted;

  .label {
    color: $color-text-body;
    margin-right: 4px;
  }

  &.rush .label,
  &.rush .value {
    color: $color-primary;
  }
}

.card-bottom {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: auto;
}

.stock-block {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;

  .progress-bar {
    height: 5px;
    background: $color-bg;
    border-radius: $radius-xs;
    overflow: hidden;

    .progress-fill {
      height: 100%;
      background: $color-accent-gradient-gold;
      transition: width 0.3s;
    }
  }

  .stock-text {
    font-size: 11px;
    color: $color-text-muted;
  }
}

.buy-btn {
  flex-shrink: 0;
  min-width: 84px;

  &.is-purchased,
  &:disabled.is-purchased {
    color: $color-text-muted;
    border-color: $color-border-gray;
    background: $color-bg;
    cursor: not-allowed;
  }
}
</style>
