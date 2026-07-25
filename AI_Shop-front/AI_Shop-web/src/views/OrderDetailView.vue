<template>
  <div class="order-detail-page" v-loading="loading">
    <template v-if="order">
      <section class="status-card card">
        <span class="status-text" :class="statusClass">{{ displayStatus }}</span>
        <p v-if="order.subject" class="status-sub">{{ order.subject }}</p>
      </section>

      <section class="info-card card">
        <h3 class="section-title">订单信息</h3>
        <dl class="info-list">
          <div class="info-row">
            <dt>订单编号</dt>
            <dd>{{ order.orderId }}</dd>
          </div>
          <div class="info-row">
            <dt>下单时间</dt>
            <dd>{{ formatTime(order.orderTime) }}</dd>
          </div>
          <div v-if="order.payOrderId" class="info-row">
            <dt>支付单号</dt>
            <dd>{{ order.payOrderId }}</dd>
          </div>
          <div v-if="order.payChannel" class="info-row">
            <dt>支付方式</dt>
            <dd>{{ order.payChannel }}</dd>
          </div>
        </dl>
      </section>

      <section class="items-card card">
        <h3 class="section-title">{{ isCouponOrder ? '优惠券信息' : '商品明细' }}</h3>
        <div v-if="itemList.length" class="goods-list">
          <component
            :is="isCouponOrder ? 'div' : 'button'"
            v-for="item in itemList"
            :key="item.orderItemId"
            :type="isCouponOrder ? undefined : 'button'"
            class="goods-row"
            @click="!isCouponOrder && goProduct(item.productId)"
          >
            <div class="goods-cover-col" :class="{ 'is-coupon': isCouponOrder }">
              <el-icon v-if="isCouponOrder" class="coupon-icon"><Ticket /></el-icon>
              <ProductImage v-else :source="item.cover" class="goods-cover" />
            </div>
            <div class="goods-info">
              <p class="goods-name">{{ item.productName }}</p>
              <p v-if="item.propertyInfo && !isCouponOrder" class="goods-sku">{{ item.propertyInfo }}</p>
              <p class="goods-remark">买家备注：{{ item.remark?.trim() || '暂无' }}</p>
              <OrderItemIdText :id="item.orderItemId" />
            </div>
            <div class="goods-price">
              <span class="price">¥{{ formatMoney(item.itemAmount) }}</span>
              <span class="qty">×{{ item.buyCount }}</span>
            </div>
            <div
              v-if="canRefundItem(item)"
              class="goods-action"
            >
              <el-button size="small" text type="danger" @click.stop="refundItem(item.orderItemId)">
                退款
              </el-button>
            </div>
          </component>
        </div>
        <el-empty v-else :description="isCouponOrder ? '暂无优惠券信息' : '暂无商品明细'" :image-size="64" />
      </section>

      <section v-if="!isCouponOrder && hasOrderCouponDiscount(order)" class="coupon-card card">
        <h3 class="section-title">优惠券</h3>
        <dl class="info-list">
          <div class="info-row">
            <dt>使用优惠券</dt>
            <dd>{{ orderCouponSummaryText(order) }}</dd>
          </div>
        </dl>
      </section>

      <section class="amount-card card">
        <OrderAmountSummary :order="order" />
      </section>

      <div v-if="showPayBtn || showLogisticsBtn" class="detail-actions">
        <el-button v-if="showPayBtn" type="primary" round @click="goPay">去支付</el-button>
        <el-button v-if="showLogisticsBtn" type="primary" plain round @click="goLogistics">查看物流</el-button>
      </div>
    </template>

    <el-empty v-else-if="!loading" description="订单不存在或无权查看" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Ticket } from '@element-plus/icons-vue';
import ProductImage from '@/components/common/ProductImage.vue';
import OrderAmountSummary from '@/components/business/OrderAmountSummary.vue';
import OrderItemIdText from '@/components/business/OrderItemIdText.vue';
import { orderApi } from '@/api/modules';
import { displayOrderStatusText } from '@/constants/backendEnums';
import { usePageRefresh } from '@/composables/pullRefresh';
import { confirmAction } from '@/utils/confirm';
import { toast } from '@/utils/toast';
import { hasOrderCouponDiscount, orderCouponSummaryText } from '@/utils/orderAmount';

const route = useRoute();
const router = useRouter();
const loading = ref(true);
const order = ref<Record<string, any> | null>(null);

const itemList = computed(() => {
  const list = order.value?.orderItemList;
  return Array.isArray(list) ? list : [];
});

const isCouponOrder = computed(() => String(order.value?.payScene) === '2');

const showPayBtn = computed(() => order.value?.orderStatus === 0 && order.value?.payOrderId);

const displayStatus = computed(() => {
  if (!order.value) return '';
  if (isCouponOrder.value && order.value.orderStatus === 3) return '已完成';
  return displayOrderStatusText(order.value);
});

const statusClass = computed(() => {
  const o = order.value;
  if (!o) return '';
  if (o.orderStatus === 0) return 'is-wait-pay';
  if (o.orderStatus === 2) return 'is-shipped';
  if (o.orderStatus === 4 || o.orderStatus === 5) return 'is-cancel';
  return '';
});

const showLogisticsBtn = computed(() => {
  if (isCouponOrder.value) return false;
  const s = order.value?.orderStatus;
  return s === 2 || s === 3 || s === 7;
});

const itemOrderStatus = (item: Record<string, any>) => Number(item.orderItemStatus ?? 1);

const canRefundItem = (item: Record<string, any>) => {
  if (isCouponOrder.value) return false;
  const s = order.value?.orderStatus;
  return (s === 1 || s === 2) && itemOrderStatus(item) === 1;
};

const refundItem = async (orderItemId: string) => {
  const ok = await confirmAction('确定要申请退款吗？退款将按原支付方式退回。', {
    title: '申请退款',
    confirmButtonText: '申请退款'
  });
  if (!ok) return;
  await orderApi.refundOrder(orderItemId);
  toast.success('退款申请已提交');
  load();
};

const formatMoney = (val: unknown) => Number(val ?? 0).toFixed(2);

const formatTime = (val: unknown) => {
  if (!val) return '--';
  if (typeof val === 'string') return val;
  const d = new Date(val as string | number);
  if (Number.isNaN(d.getTime())) return String(val);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};

const goProduct = (productId: string) => {
  if (productId) router.push(`/product/${productId}`);
};

const goLogistics = () => {
  router.push(`/order/${route.params.orderId}/logistics`);
};

const goPay = () => {
  const payOrderId = order.value?.payOrderId;
  if (payOrderId) router.push(`/payment/${payOrderId}`);
};

const load = async () => {
  loading.value = true;
  try {
    order.value = (await orderApi.getMyOrderDetail(String(route.params.orderId))) || null;
  } finally {
    loading.value = false;
  }
};

onMounted(load);
usePageRefresh(load);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.order-detail-page {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-bottom: 16px;
}

.card {
  padding: 14px 16px;
  background: $color-card;
  border-radius: $radius-card;
  border: 1px solid $color-border-light;
}

.status-card {
  text-align: center;
  padding: 20px 16px;

  .status-text {
    font-size: 18px;
    font-weight: 700;
    color: $color-primary;

    &.is-wait-pay {
      color: $color-price;
    }

    &.is-shipped {
      color: $color-primary;
    }

    &.is-cancel {
      color: $color-text-muted;
    }
  }

  .status-sub {
    margin: 8px 0 0;
    font-size: 13px;
    color: $color-text-muted;
  }
}

.section-title {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
  color: $color-text-title;
}

.info-list {
  margin: 0;
}

.info-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  font-size: 13px;
  border-bottom: 1px solid $color-border-light;

  &:last-child {
    border-bottom: none;
  }

  dt {
    flex-shrink: 0;
    margin: 0;
    color: $color-text-muted;
    font-weight: 400;
  }

  dd {
    margin: 0;
    text-align: right;
    color: $color-text-title;
    word-break: break-all;
  }
}

.goods-list {
  display: flex;
  flex-direction: column;
}

.goods-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 10px 0;
  border: none;
  border-bottom: 1px solid $color-border-light;
  background: transparent;
  text-align: left;
  cursor: pointer;

  &:last-child {
    border-bottom: none;
  }

  &:active {
    background: rgba($color-primary, 0.04);
  }
}

.goods-cover-col {
  flex: 0 0 72px;
  width: 72px;
  aspect-ratio: 1;
  border-radius: $radius-xs;
  overflow: hidden;

  :deep(.product-image) {
    width: 100% !important;
    height: 100% !important;
    border-radius: $radius-xs;
  }

  &.is-coupon {
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, rgba($color-primary, 0.12), rgba($color-price, 0.1));

    .coupon-icon {
      font-size: 32px;
      color: $color-primary;
    }
  }
}

.goods-info {
  flex: 1;
  min-width: 0;

  .goods-name {
    margin: 0 0 4px;
    font-size: 13px;
    line-height: 1.4;
    color: $color-text-title;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .goods-sku {
    margin: 0;
    font-size: 11px;
    color: $color-text-muted;
  }

  .goods-remark {
    margin: 4px 0 0;
    font-size: 11px;
    line-height: 1.4;
    color: #000;
    word-break: break-all;
  }
}

.goods-price {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;

  .price {
    font-size: 14px;
    font-weight: 700;
    color: $color-price;
  }

  .qty {
    font-size: 12px;
    color: $color-text-muted;
  }
}

.goods-action {
  flex-shrink: 0;
  display: flex;
  align-items: flex-end;
  padding-left: 8px;
}

.amount-card {
  .amount-row {
    display: flex;
    align-items: baseline;
    justify-content: flex-end;
    gap: 8px;
    font-size: 14px;
    color: $color-text-body;

    .amount {
      font-size: 20px;
      font-weight: 700;
      color: $color-price;
    }
  }
}

.detail-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  padding: 4px 0 8px;
}
</style>
