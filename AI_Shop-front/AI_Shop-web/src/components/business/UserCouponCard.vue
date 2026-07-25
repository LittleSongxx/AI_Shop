<template>
  <article class="user-coupon-card" :class="{ 'is-used': coupon.status === 1, 'is-expired': coupon.status === 2 }">
    <div class="card-left">
      <p class="main-value">{{ couponMainValue(coupon) }}</p>
      <p class="condition">{{ couponConditionText(coupon) }}</p>
    </div>
    <div class="card-right">
      <div class="head-row">
        <h4 class="name">{{ coupon.couponName || '优惠券' }}</h4>
        <span class="type-tag">{{ couponTypeLabel(coupon.couponType) }}</span>
      </div>
      <p class="time-line">有效期至 {{ formatCouponTime(coupon.validEndTime) }}</p>
      <p class="status-line">{{ USER_COUPON_STATUS_MAP[coupon.status] || '未知' }}</p>
    </div>
  </article>
</template>

<script setup lang="ts">
import {
  USER_COUPON_STATUS_MAP,
  couponConditionText,
  couponMainValue,
  couponTypeLabel,
  formatCouponTime
} from '@/utils/coupon';

defineProps<{ coupon: Record<string, any> }>();
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.user-coupon-card {
  display: flex;
  background: $color-card;
  border-radius: $radius-card;
  overflow: hidden;
  box-shadow: $shadow-card;
  margin-bottom: 10px;

  &.is-used,
  &.is-expired {
    opacity: 0.72;
    filter: grayscale(0.2);
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
  padding: 12px 8px;
  text-align: center;

  .main-value {
    margin: 0;
    font-size: 22px;
    font-weight: 700;
    line-height: 1.1;
  }

  .condition {
    margin: 6px 0 0;
    font-size: 11px;
    opacity: 0.92;
    line-height: 1.3;
  }
}

.card-right {
  flex: 1;
  min-width: 0;
  padding: 12px 14px;

  .head-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 6px;
  }

  .name {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    color: $color-text-title;
    line-height: 1.35;
  }

  .type-tag {
    flex-shrink: 0;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: $radius-pill;
    background: $color-gold-soft;
    border: 1px solid $color-gold-muted;
    color: $color-gold;
  }

  .time-line,
  .status-line {
    margin: 0;
    font-size: 12px;
    color: $color-text-muted;
    line-height: 1.4;
  }

  .status-line {
    margin-top: 4px;
    color: $color-text-body;
  }
}
</style>
