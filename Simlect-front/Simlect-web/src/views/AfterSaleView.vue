<template>
  <div class="after-sale-page">
    <div class="after-sale-tabs card-flat tabs-scroll-single">
      <el-tabs v-model="tab" @tab-change="onTabChange">
        <el-tab-pane label="全部" name="all" />
        <el-tab-pane label="已退款" name="6" />
        <el-tab-pane label="部分退款" name="7" />
      </el-tabs>
    </div>

    <div ref="scrollRoot" class="after-sale-body">
      <el-skeleton :loading="loading && !list.length" animated :count="2">
        <template #default>
          <div v-if="list.length" class="order-list">
            <SwipeDeleteRow
              v-for="order in list"
              :key="order.orderId"
              :deletable="canDeleteOrder(order)"
              :open="openSwipeId === order.orderId"
              @open="openSwipeId = order.orderId"
              @close="onSwipeClose(order.orderId)"
              @delete="removeOrder(order.orderId)"
            >
              <section class="order-card card">
                <header class="order-head">
                  <span class="order-no">订单号：{{ order.orderId }}</span>
                  <span class="order-status" :class="statusClass(order)">
                    {{ displayStatus(order) }}
                  </span>
                </header>

                <div class="goods-list">
                  <component
                    :is="isCouponOrder(order) ? 'div' : 'button'"
                    v-for="item in order.orderItemList || []"
                    :key="item.orderItemId"
                    :type="isCouponOrder(order) ? undefined : 'button'"
                    class="goods-row"
                    @click="!isCouponOrder(order) && goProduct(item.productId)"
                  >
                    <div class="goods-cover-col" :class="{ 'is-coupon': isCouponOrder(order) }">
                      <el-icon v-if="isCouponOrder(order)" class="coupon-icon"><Ticket /></el-icon>
                      <ProductImage v-else :source="item.cover" class="goods-cover" />
                    </div>
                    <div class="goods-info">
                      <p class="goods-name">{{ item.productName }}</p>
                      <p v-if="item.propertyInfo && !isCouponOrder(order)" class="goods-sku">
                        {{ item.propertyInfo }}
                      </p>
                      <OrderItemIdText :id="item.orderItemId" />
                      <p v-if="Number(item.orderItemStatus) === 0" class="item-refund-tag">已退款</p>
                    </div>
                    <div class="goods-price">
                      <span class="price">¥{{ formatMoney(item.itemAmount) }}</span>
                      <span class="qty">×{{ item.buyCount }}</span>
                    </div>
                    <div v-if="canRefundItem(order, item)" class="goods-action">
                      <el-button size="small" text type="danger" @click.stop="refundItem(item.orderItemId)">
                        退款
                      </el-button>
                    </div>
                  </component>
                </div>

                <footer class="order-foot">
                  <div class="pay-line">
                    <span class="label">实付款</span>
                    <span class="amount">¥{{ formatMoney(order.amount) }}</span>
                  </div>
                  <div class="order-ops">
                    <el-button
                      v-if="showLogistics(order)"
                      size="small"
                      @click.stop="goLogistics(order.orderId)"
                    >
                      查看物流
                    </el-button>
                    <el-button size="small" link @click.stop="goOrderDetail(order.orderId)">
                      订单详情
                    </el-button>
                  </div>
                </footer>
              </section>
            </SwipeDeleteRow>
          </div>
          <el-empty v-else-if="loadError && !loading" :description="loadError" class="orders-empty">
            <el-button type="primary" @click="onTabChange">重试</el-button>
          </el-empty>
          <el-empty v-else-if="!loading" description="暂无售后订单" class="orders-empty" />
          <div ref="sentinelRef" class="load-sentinel" />
          <p v-if="loadingMore" class="load-tip">加载中…</p>
          <p v-else-if="finished && list.length" class="load-tip muted">没有更多了</p>
        </template>
      </el-skeleton>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, nextTick } from 'vue';
import { useRouter } from 'vue-router';
import { Ticket } from '@element-plus/icons-vue';
import ProductImage from '@/components/common/ProductImage.vue';
import OrderItemIdText from '@/components/business/OrderItemIdText.vue';
import SwipeDeleteRow from '@/components/business/SwipeDeleteRow.vue';
import { usePageListCache } from '@/composables/usePageListCache';
import { usePageRefresh } from '@/composables/pullRefresh';
import { useDevice } from '@/composables/useDevice';
import { orderApi } from '@/api/modules';
import { orderStatusLabel } from '@/constants/backendEnums';
import { confirmAction } from '@/utils/confirm';
import { toast } from '@/utils/toast';

const router = useRouter();

const tab = ref('all');
const pageNo = ref(0);
const pageTotal = ref(1);
const list = ref<any[]>([]);
const loading = ref(false);
const loadingMore = ref(false);
const loadError = ref('');
const finished = ref(false);
const scrollRoot = ref<HTMLElement>();
const sentinelRef = ref<HTMLElement>();
const openSwipeId = ref<string | null>(null);
let observer: IntersectionObserver | null = null;

const { isMobile } = useDevice();
const usesWindowScroll = () => isMobile.value;

const isCouponOrder = (order: Record<string, any>) => String(order.payScene) === '2';

const formatMoney = (val: unknown) => Number(val ?? 0).toFixed(2);

const displayStatus = (order: Record<string, any>) =>
  order.orderStatusName || orderStatusLabel(order.orderStatus);

const statusClass = (order: Record<string, any>) => {
  const status = Number(order.orderStatus);
  if (status === 6) return 'is-refunded';
  if (status === 7) return 'is-partial-refund';
  return '';
};

const canDeleteOrder = (order: Record<string, any>) => Number(order.orderStatus) === 6;

const showLogistics = (order: Record<string, any>) =>
  !isCouponOrder(order) && Number(order.orderStatus) === 7;

const canRefundItem = (order: Record<string, any>, item: Record<string, any>) =>
  !isCouponOrder(order) &&
  (Number(order.orderStatus) === 1 ||
    Number(order.orderStatus) === 2 ||
    Number(order.orderStatus) === 7) &&
  Number(item.orderItemStatus) === 1;

const mergeOrders = (rows: any[]) => {
  const map = new Map<string, any>();
  for (const row of rows) {
    if (row?.orderId) map.set(row.orderId, row);
  }
  return [...map.values()].sort((a, b) => {
    const ta = new Date(a.orderTime || 0).getTime();
    const tb = new Date(b.orderTime || 0).getTime();
    return tb - ta;
  });
};

const fetchPage = async (next: number, status?: number) => {
  if (status != null) {
    return orderApi.loadMyOrder({ pageNo: next, status });
  }
  const [r6, r7] = await Promise.all([
    orderApi.loadMyOrder({ pageNo: next, status: 6 }),
    orderApi.loadMyOrder({ pageNo: next, status: 7 })
  ]);
  return {
    list: mergeOrders([...(r6?.list || []), ...(r7?.list || [])]),
    pageNo: Math.max(r6?.pageNo ?? next, r7?.pageNo ?? next),
    pageTotal: Math.max(r6?.pageTotal ?? next, r7?.pageTotal ?? next)
  };
};

const setupObserver = () => {
  observer?.disconnect();
  if (!sentinelRef.value) return;
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) loadMore();
    },
    { root: usesWindowScroll() ? null : scrollRoot.value, rootMargin: '100px' }
  );
  observer.observe(sentinelRef.value);
};

const pageCache = usePageListCache({
  cacheKey: () => `/after-sale|tab=${tab.value}`,
  getState: () => ({
    tab: tab.value,
    list: list.value,
    pageNo: pageNo.value,
    pageTotal: pageTotal.value,
    finished: finished.value
  }),
  setState: (state) => {
    tab.value = String(state.tab ?? 'all');
    list.value = (state.list as any[]) || [];
    pageNo.value = Number(state.pageNo) || 0;
    pageTotal.value = Number(state.pageTotal) || 1;
    finished.value = !!state.finished;
    loading.value = false;
    loadingMore.value = false;
  },
  afterRestore: setupObserver
});

const loadMore = async () => {
  if (loadingMore.value || finished.value) return;
  if (pageNo.value >= pageTotal.value && pageNo.value > 0) {
    finished.value = true;
    return;
  }

  loadingMore.value = true;
  if (!list.value.length) loading.value = true;
  loadError.value = '';
  try {
    const next = pageNo.value + 1;
    const status = tab.value === 'all' ? undefined : Number(tab.value);
    const r = await fetchPage(next, status);
    const chunk = r?.list || [];
    if (next === 1) list.value = chunk;
    else list.value = mergeOrders(list.value.concat(chunk));
    pageNo.value = r?.pageNo ?? next;
    pageTotal.value = r?.pageTotal ?? pageNo.value;
    finished.value = pageNo.value >= pageTotal.value;
  } catch (e: any) {
    loadError.value = e?.info || e?.message || '售后订单加载失败，请稍后重试';
  } finally {
    loadingMore.value = false;
    loading.value = false;
    nextTick(() => setupObserver());
  }
};

const onTabChange = () => {
  pageCache.clear();
  pageNo.value = 0;
  pageTotal.value = 1;
  finished.value = false;
  list.value = [];
  if (usesWindowScroll()) window.scrollTo(0, 0);
  else if (scrollRoot.value) scrollRoot.value.scrollTop = 0;
  loadMore();
};

const refundItem = async (orderItemId: string) => {
  const ok = await confirmAction('确定要申请退款吗？退款将按原支付方式退回。', {
    title: '申请退款',
    confirmButtonText: '申请退款'
  });
  if (!ok) return;
  await orderApi.refundOrder(orderItemId);
  toast.success('退款申请已提交');
  onTabChange();
};

const goProduct = (productId: string) => {
  if (productId) router.push(`/product/${productId}`);
};

const goOrderDetail = (orderId: string) => {
  router.push(`/order/${orderId}`);
};

const goLogistics = (orderId: string) => {
  router.push(`/order/${orderId}/logistics`);
};

const onSwipeClose = (orderId: string) => {
  if (openSwipeId.value === orderId) openSwipeId.value = null;
};

const removeOrder = async (orderId: string) => {
  const ok = await confirmAction('删除后订单将从列表中移除，确定删除吗？', {
    title: '删除订单',
    confirmButtonText: '删除'
  });
  if (!ok) return;
  await orderApi.deleteOrder(orderId);
  list.value = list.value.filter((o) => o.orderId !== orderId);
  if (openSwipeId.value === orderId) openSwipeId.value = null;
  toast.success('订单已删除');
};

onMounted(async () => {
  const restored = await pageCache.tryRestore();
  if (!restored) await loadMore();
  setupObserver();
});

usePageRefresh(onTabChange, {
  getScrollEl: () => (usesWindowScroll() ? null : scrollRoot.value)
});

onUnmounted(() => {
  observer?.disconnect();
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.after-sale-page {
  min-height: calc(100vh - var(--sub-top-height, 60px));
}

.after-sale-tabs {
  flex-shrink: 0;
  padding: 0 4px;
  margin-bottom: 10px;
  background: $color-card;
  border-radius: $radius-card;
}

.after-sale-body {
  min-height: 120px;
}

.order-list {
  position: relative;
  z-index: 0;
}

.order-list :deep(.swipe-delete-row) {
  border-radius: $radius-card;
}

.order-card {
  padding: 0;
  overflow: hidden;
  border-radius: $radius-card;
}

.order-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  font-size: 12px;
  color: $color-text-body;
  background: #fafafa;
  border-bottom: 1px solid $color-border;

  .order-no {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .order-status {
    flex-shrink: 0;
    font-weight: 600;

    &.is-refunded {
      color: $color-text-muted;
    }

    &.is-partial-refund {
      color: $color-price;
    }
  }
}

.goods-list {
  padding: 0 12px;
}

.goods-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 12px 0;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;

  & + & {
    border-top: 1px solid $color-border;
  }
}

.goods-cover-col {
  width: 72px;
  height: 72px;
  flex-shrink: 0;
  border-radius: 8px;
  overflow: hidden;
  background: $color-bg-subtle;

  &.is-coupon {
    display: grid;
    place-items: center;
    background: linear-gradient(135deg, #fff7e6, #ffe7ba);

    .coupon-icon {
      font-size: 28px;
      color: $color-price;
    }
  }
}

.goods-cover {
  width: 100%;
  height: 100%;
}

.goods-info {
  flex: 1;
  min-width: 0;
}

.goods-name {
  margin: 0;
  font-size: 14px;
  line-height: 1.4;
  color: $color-text-title;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.goods-sku {
  margin: 4px 0 0;
  font-size: 12px;
  color: $color-text-muted;
}

.item-refund-tag {
  margin: 4px 0 0;
  font-size: 12px;
  color: $color-text-muted;
}

.goods-price {
  flex-shrink: 0;
  text-align: right;
  font-size: 13px;

  .price {
    display: block;
    color: $color-text-title;
    font-weight: 600;
  }

  .qty {
    color: $color-text-muted;
    font-size: 12px;
  }
}

.goods-action {
  flex-shrink: 0;
  align-self: center;
}

.order-foot {
  padding: 10px 12px 12px;
  border-top: 1px solid $color-border;
}

.pay-line {
  display: flex;
  justify-content: flex-end;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 10px;
  font-size: 13px;

  .label {
    color: $color-text-body;
  }

  .amount {
    font-size: 16px;
    font-weight: 700;
    color: $color-price;
  }
}

.order-ops {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.load-sentinel {
  height: 1px;
}

.load-tip {
  text-align: center;
  padding: 12px 0;
  font-size: 13px;
  color: $color-text-body;

  &.muted {
    color: $color-text-muted;
  }
}

.orders-empty {
  padding: 32px 0;
}
</style>
