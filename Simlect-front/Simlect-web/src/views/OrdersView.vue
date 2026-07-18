<template>
  <div class="orders-page">
    <div ref="tabsRef" class="orders-tabs card-flat tabs-scroll-single">
      <el-tabs v-model="tab" @tab-change="onTabChange">
        <el-tab-pane label="全部" name="" />
        <el-tab-pane label="待付款" name="0" />
        <el-tab-pane label="待发货" name="1" />
        <el-tab-pane label="待收货" name="2" />
        <el-tab-pane label="已完成" name="completed" />
        <el-tab-pane label="待评价" name="evaluate" />
      </el-tabs>
    </div>

    <div ref="scrollRoot" class="orders-body">
      <el-skeleton :loading="loading && !list.length" animated :count="2">
        <template #default>
          <div v-if="displayList.length" class="order-list">
            <SwipeDeleteRow
              v-for="order in displayList"
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
                      <p class="goods-remark">买家备注：{{ item.remark?.trim() || '暂无' }}</p>
                      <OrderItemIdText :id="item.orderItemId" />
                    </div>
                    <div class="goods-price">
                      <span class="price">¥{{ formatMoney(item.itemAmount) }}</span>
                      <span class="qty">×{{ item.buyCount }}</span>
                    </div>
                    <div
                      v-if="canRefundItem(order, item)"
                      class="goods-action"
                    >
                      <el-button size="small" text type="danger" @click.stop="refundItem(item.orderItemId)">
                        退款
                      </el-button>
                    </div>
                  </component>
                </div>

                <footer class="order-foot">
                  <OrderAmountSummary :order="order" compact />
                  <div class="order-ops">
                    <el-button
                      v-if="order.orderStatus === 0"
                      size="small"
                      @click.stop="cancel(order.orderId)"
                    >
                      取消订单
                    </el-button>
                    <el-button
                      v-if="order.orderStatus === 0"
                      type="primary"
                      size="small"
                      @click.stop="goPay(order.payOrderId)"
                    >
                      去支付
                    </el-button>
                    <el-button
                      v-if="order.orderStatus === 2"
                      type="primary"
                      size="small"
                      @click.stop="confirmReceive(order.orderId)"
                    >
                      确认收货
                    </el-button>
                    <el-button
                      v-if="canComment(order)"
                      type="primary"
                      size="small"
                      @click.stop="openComment(order.orderId)"
                    >
                      评价
                    </el-button>
                    <el-button
                      v-if="canRecomment(order)"
                      size="small"
                      @click.stop="openRecomment(order.orderId)"
                    >
                      追评
                    </el-button>
                    <el-button
                      v-if="canViewComment(order)"
                      size="small"
                      @click.stop="openCommentPreview(order.orderId)"
                    >
                      查看评价
                    </el-button>
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
          <el-empty v-else-if="!loading" description="暂无相关订单" class="orders-empty" />
          <div ref="sentinelRef" class="load-sentinel" />
          <p v-if="loadingMore" class="load-tip">加载中…</p>
          <p v-else-if="finished && displayList.length" class="load-tip muted">没有更多了</p>
        </template>
      </el-skeleton>
    </div>

    <OrderCommentDialog ref="commentDialogRef" @success="onTabChange" />
    <OrderRecommentDialog ref="recommentDialogRef" @success="onTabChange" />
    <OrderCommentPreviewDialog ref="commentPreviewDialogRef" />
  </div>

</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { Ticket } from '@element-plus/icons-vue';
import ProductImage from '@/components/common/ProductImage.vue';
import SwipeDeleteRow from '@/components/business/SwipeDeleteRow.vue';
import OrderAmountSummary from '@/components/business/OrderAmountSummary.vue';
import OrderItemIdText from '@/components/business/OrderItemIdText.vue';
const OrderCommentDialog = defineAsyncComponent(
  () => import('@/components/business/OrderCommentDialog.vue')
);
const OrderRecommentDialog = defineAsyncComponent(
  () => import('@/components/business/OrderRecommentDialog.vue')
);
const OrderCommentPreviewDialog = defineAsyncComponent(
  () => import('@/components/business/OrderCommentPreviewDialog.vue')
);
import { usePageListCache } from '@/composables/usePageListCache';
import { usePageRefresh } from '@/composables/pullRefresh';
import { useDevice } from '@/composables/useDevice';
import { orderApi } from '@/api/modules';
import { displayOrderStatusText } from '@/constants/backendEnums';
import { confirmAction } from '@/utils/confirm';
import { toast } from '@/utils/toast';

const router = useRouter();
const route = useRoute();

const initTab = (() => {
  const q = route.query.status as string | undefined;
  if (q === '3') return 'completed';
  if (q === '8') return 'evaluate';
  return q || '';
})();

const tab = ref(initTab);
const apiStatus = computed(() => {
  if (tab.value === 'completed') return '3';
  if (tab.value === 'evaluate') return '8';
  return tab.value || undefined;
});
const pageNo = ref(0);
const pageTotal = ref(1);
const list = ref<any[]>([]);
const loading = ref(false);
const loadingMore = ref(false);
const loadError = ref('');
const finished = ref(false);
const scrollRoot = ref<HTMLElement>();
const tabsRef = ref<HTMLElement>();
const sentinelRef = ref<HTMLElement>();
const commentDialogRef = ref<InstanceType<typeof OrderCommentDialog>>();
const recommentDialogRef = ref<InstanceType<typeof OrderRecommentDialog>>();
const commentPreviewDialogRef = ref<InstanceType<typeof OrderCommentPreviewDialog>>();
const openSwipeId = ref<string | null>(null);
let observer: IntersectionObserver | null = null;
let tabsResizeObserver: ResizeObserver | null = null;

const { isMobile } = useDevice();

const usesWindowScroll = () => isMobile.value;

const scrollPageToTop = () => {
  if (usesWindowScroll()) {
    window.scrollTo(0, 0);
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
    return;
  }
  if (scrollRoot.value) scrollRoot.value.scrollTop = 0;
};

const syncOrdersTabsInset = () => {
  document.documentElement.style.removeProperty('--orders-tabs-height');
};

const isCouponOrder = (order: Record<string, any>) => String(order.payScene) === '2';

const displayList = computed(() => {
  if (tab.value === 'evaluate') {
    return list.value.filter((o) => !isCouponOrder(o) && Number(o.commentStatus) === 0);
  }
  return list.value;
});

const canDeleteOrder = (order: Record<string, any>) => {
  const s = Number(order.orderStatus);
  return s === 3 || s === 4 || s === 5 || s === 6;
};

const formatMoney = (val: unknown) => Number(val ?? 0).toFixed(2);
const displayStatus = (order: Record<string, any>) => {
  if (tab.value === 'completed' && order.orderStatus === 3) return '已完成';
  const text = displayOrderStatusText(order);
  if (isCouponOrder(order)) {
    if (order.orderStatus === 3) return '已完成';
    if (order.orderStatus === 4) return '已取消';
  }
  return text;
};

const statusClass = (order: Record<string, any>) => {
  const status = order.orderStatus;
  if (status === 3) {
    if (tab.value === 'completed') return '';
    if (isCouponOrder(order)) return '';
    if (order.commentStatus === 0) return 'is-comment-pending';
    if (order.commentStatus === 1) return 'is-commented';
    if (order.commentStatus === 2) return 'is-recommented';
  }
  if (status === 0) return 'is-wait-pay';
  if (status === 2) return 'is-shipped';
  if (status === 4 || status === 5) return 'is-cancel';
  return '';
};

const canComment = (order: Record<string, any>) =>
  !isCouponOrder(order) && order.orderStatus === 3 && Number(order.commentStatus) === 0;

const canRecomment = (order: Record<string, any>) =>
  !isCouponOrder(order) && order.orderStatus === 3 && Number(order.commentStatus) === 1;

const canViewComment = (order: Record<string, any>) =>
  !isCouponOrder(order) &&
  order.orderStatus === 3 &&
  (Number(order.commentStatus) === 1 || Number(order.commentStatus) === 2);

const showLogistics = (order: Record<string, any>) =>
  !isCouponOrder(order) &&
  (order.orderStatus === 2 || order.orderStatus === 3 || order.orderStatus === 7);

const canRefundItem = (order: Record<string, any>, item: Record<string, any>) =>
  !isCouponOrder(order) &&
  (order.orderStatus === 1 || order.orderStatus === 2) &&
  Number(item.orderItemStatus) === 1;

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
  cacheKey: () => `/orders|tab=${tab.value}`,
  getState: () => ({
    tab: tab.value,
    list: list.value,
    pageNo: pageNo.value,
    pageTotal: pageTotal.value,
    finished: finished.value
  }),
  setState: (state) => {
    tab.value = String(state.tab ?? '');
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
    const r = await orderApi.loadMyOrder({
      pageNo: next,
      status: apiStatus.value
    });
    const chunk = r?.list || [];
    if (next === 1) list.value = chunk;
    else list.value = list.value.concat(chunk);
    pageNo.value = r?.pageNo ?? next;
    pageTotal.value = r?.pageTotal ?? pageNo.value;
    finished.value = pageNo.value >= pageTotal.value;
  } catch (e: any) {
    loadError.value = e?.info || e?.message || '订单加载失败，请稍后重试';
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
  scrollPageToTop();

  const queryStatus = apiStatus.value || undefined;
  router.replace({ path: route.path, query: queryStatus ? { status: queryStatus } : {} });
  loadMore();
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

const cancel = async (id: string) => {
  const ok = await confirmAction('取消后订单将关闭，确定要取消该订单吗？', {
    title: '取消订单',
    confirmButtonText: '取消订单'
  });
  if (!ok) return;
  await orderApi.cancelOrder(id);
  toast.success('订单已取消');
  onTabChange();
};

const confirmReceive = async (id: string) => {
  const ok = await confirmAction('确认收货后将无法发起退款，确定已收到商品吗？', {
    title: '确认收货',
    confirmButtonText: '确认收货'
  });
  if (!ok) return;
  await orderApi.confirmOrder(id);
  toast.success('已确认收货');
  onTabChange();
};

const goPay = (id: string) => {
  if (id) router.push(`/payment/${id}`);
};

const openComment = (orderId: string) => {
  commentDialogRef.value?.show(orderId);
};

const openRecomment = (orderId: string) => {
  recommentDialogRef.value?.show(orderId);
};

const openCommentPreview = (orderId: string) => {
  commentPreviewDialogRef.value?.show(orderId);
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
  syncOrdersTabsInset();
  tabsResizeObserver = new ResizeObserver(syncOrdersTabsInset);
  if (tabsRef.value) tabsResizeObserver.observe(tabsRef.value);

  const restored = await pageCache.tryRestore();
  if (!restored) {
    await loadMore();
  }
  setupObserver();
});

usePageRefresh(onTabChange, {
  getScrollEl: () => (usesWindowScroll() ? null : scrollRoot.value)
});

onUnmounted(() => {
  observer?.disconnect();
  tabsResizeObserver?.disconnect();
  document.documentElement.style.removeProperty('--orders-tabs-height');
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.orders-page {
  min-height: calc(100vh - var(--sub-top-height, 60px));
}

.orders-tabs {
  flex-shrink: 0;
  padding: 0 4px;
  margin-bottom: 10px;
  background: $color-card;
  border-radius: $radius-card;
}

.orders-body {
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

    &.is-comment-pending {
      color: $color-price;
    }

    &.is-commented {
      color: $color-primary;
    }

    &.is-recommented {
      color: $color-text-muted;
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
  padding: 10px 0;
  border: none;
  border-bottom: 1px solid $color-border;
  background: transparent;
  text-align: left;
  cursor: pointer;

  &:last-child {
    border-bottom: none;
  }

  &:hover {
    background: rgba($color-primary, 0.04);
  }
}

.goods-cover-col {
  flex: 0 0 25%;
  width: 25%;
  max-width: 76px;
  aspect-ratio: 1;
  border-radius: $radius-sm;
  overflow: hidden;

  :deep(.product-image) {
    width: 100% !important;
    height: 100% !important;
    border-radius: $radius-sm;
  }

  &.is-coupon {
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, rgba($color-primary, 0.12), rgba($color-price, 0.1));

    .coupon-icon {
      font-size: 32px;
      color: rgba($color-primary, 0.45);
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
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
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

.order-foot {
  padding: 10px 12px 12px;
  border-top: 1px solid $color-border;

  :deep(.order-amount-summary.compact) {
    margin-bottom: 8px;
  }

  .order-ops {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
  }
}

.orders-empty {
  padding: 48px 0;
}

.load-sentinel {
  height: 1px;
}

.load-tip {
  text-align: center;
  font-size: 12px;
  color: $color-text-muted;
  padding: 12px 0;

  &.muted {
    opacity: 0.85;
  }
}

@media (max-width: $breakpoint-mobile) {
  .orders-page {
    min-height: auto;
    max-height: none;
    padding-top: 0;
  }

  .orders-tabs {
    position: sticky;
    top: var(--sub-top-height, 60px);
    z-index: 99;
    margin: 0;
    padding: 0;
    border-radius: 0;
    border-bottom: 1px solid $color-border;
    box-shadow: none;
    background: #fff;
  }

  .orders-tabs.tabs-scroll-single {
    :deep(.el-tabs__header) {
      margin-bottom: 0;
    }

    :deep(.el-tabs__content) {
      display: none;
    }

    :deep(.el-tabs__nav-scroll) {
      overflow-x: hidden;
    }

    :deep(.el-tabs__nav) {
      width: 100%;
      display: flex;
      justify-content: space-between;
    }

    :deep(.el-tabs__item) {
      flex: 1;
      min-width: 0;
      padding: 0 4px;
      font-size: 13px;
    }
  }

  .orders-body {
    overflow: visible;
  }

  .order-list :deep(.swipe-delete-row) {
    z-index: 0;
  }
}

.order-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

@media (min-width: #{$breakpoint-mobile + 1}) {
  .orders-page {
    display: flex;
    flex-direction: column;
    max-height: calc(100vh - var(--sub-top-height, 60px));
  }

  .orders-body {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }
}
</style>
