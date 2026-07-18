<template>
  <div class="agent-orders">
    <div v-for="order in list" :key="order.orderId" class="order-block">
      <div class="order-meta">
        <span class="order-id" :title="order.orderId">订单号 {{ order.orderId }}</span>
        <span class="order-status">{{ order.orderStatusName || statusText(order.orderStatus) }}</span>
      </div>
      <div v-for="item in order.orderItemList || []" :key="item.orderItemId" class="item-row">
        <div class="item-cover" :class="{ 'is-coupon': isCouponOrder(order) }">
          <el-icon v-if="isCouponOrder(order)" class="coupon-icon"><Ticket /></el-icon>
          <ProductImage v-else :source="item.cover" width="48" height="48" />
        </div>
        <div class="item-info">
          <p class="name">{{ item.productName }}</p>
          <p v-if="item.propertyInfo && !isCouponOrder(order)" class="sku">{{ item.propertyInfo }}</p>
          <p v-if="item.orderItemId" class="item-id" :title="item.orderItemId">
            订单项 ID {{ item.orderItemId }}
          </p>
        </div>
      </div>
    </div>
    <p v-if="!list.length" class="empty">暂无订单信息</p>
  </div>
</template>

<script setup lang="ts">
import { Ticket } from '@element-plus/icons-vue';
import ProductImage from '@/components/common/ProductImage.vue';
import { orderStatusLabel } from '@/constants/backendEnums';

defineProps<{ list: Record<string, any>[] }>();

const COUPON_ORDER_PAY_SCENE = '2';
const COUPON_ORDER_PROPERTY = '优惠券秒杀';

const isCouponOrder = (order: Record<string, any>) =>
  String(order.payScene) === COUPON_ORDER_PAY_SCENE
  || (order.orderItemList || []).some((item: Record<string, any>) => item?.propertyInfo === COUPON_ORDER_PROPERTY);

const statusText = (s?: number) => (s != null ? orderStatusLabel(s) : '订单');
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.agent-orders {
  max-height: 300px;
  overflow-y: auto;
  font-size: 12px;
  line-height: 1.35;
  color: $color-text-body;
}

.order-block {
  padding: 8px 0;
  border-bottom: 1px solid $color-border;

  &:last-child {
    border-bottom: none;
  }
}

.order-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 6px;
  min-width: 0;
}

.order-id {
  flex: 1;
  min-width: 0;
  font-size: 10px;
  line-height: 1.3;
  color: $color-text-muted;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.order-status {
  flex-shrink: 0;
  max-width: 42%;
  font-size: 10px;
  line-height: 1.2;
  font-weight: 500;
  color: $color-text-body;
  padding: 2px 6px;
  border-radius: $radius-xs;
  background: $color-surface-inset;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 6px;
}

.item-cover {
  flex-shrink: 0;
  width: 48px;
  height: 48px;
  border-radius: $radius-xs;
  overflow: hidden;

  :deep(.product-image) {
    width: 48px !important;
    height: 48px !important;
    border-radius: $radius-xs;
  }

  &.is-coupon {
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, rgba($color-primary, 0.12), rgba($color-price, 0.1));

    .coupon-icon {
      font-size: 26px;
      color: $color-primary;
    }
  }
}

.item-info {
  flex: 1;
  min-width: 0;

  .name {
    margin: 0;
    font-size: 12px;
    color: $color-text-title;
    line-height: 1.35;
  }

  .sku {
    margin: 2px 0 0;
    font-size: 11px;
    color: $color-text-muted;
  }

  .item-id {
    margin: 4px 0 0;
    font-size: 10px;
    line-height: 1.35;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    color: $color-text-muted;
    word-break: break-all;
  }
}

.empty {
  margin: 0;
  font-size: 12px;
  color: $color-text-muted;
  text-align: center;
}
</style>
