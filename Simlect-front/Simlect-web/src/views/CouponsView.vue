<template>
  <div class="coupons-page">
    <header class="page-header card-flat">
      <h2 class="page-title">优惠券秒杀</h2>
      <div class="rush-notice">
        <el-icon class="notice-icon"><InfoFilled /></el-icon>
        <p class="notice-text">
          所有优惠券均为 <strong>0.01 元</strong> 抢购，每人每张<strong>不可重复购买</strong>。
        </p>
      </div>
      <div class="search-row">
        <el-input
          v-model="keyword"
          placeholder="搜索优惠券"
          clearable
          class="search-input"
          @keyup.enter="resetAndLoad"
        >
          <template #suffix>
            <el-icon class="search-icon" @click="resetAndLoad"><Search /></el-icon>
          </template>
        </el-input>
      </div>
    </header>

    <nav class="status-tabs card-flat toolbar-row">
      <button
        v-for="tab in tabs"
        :key="tab.value"
        type="button"
        class="tab-btn"
        :class="{ active: statusTab === tab.value }"
        @click="switchTab(tab.value)"
      >
        {{ tab.label }}
      </button>
    </nav>

    <div ref="scrollRoot" v-loading="!!rushingCouponId" class="coupon-list-scroll">
      <CouponCard
        v-for="c in list"
        :key="c.couponId"
        :coupon="c"
        type="available"
        :receiving="rushingCouponId === c.couponId"
        @receive="receive"
      />
      <el-empty v-if="!list.length && !loading" description="暂无优惠券" class="list-empty" />
      <div ref="sentinelRef" class="load-sentinel" />
      <p v-if="loading" class="load-tip">加载中…</p>
      <p v-else-if="finished && list.length" class="load-tip">没有更多了</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { InfoFilled, Search } from '@element-plus/icons-vue';
import CouponCard from '@/components/business/CouponCard.vue';
import { usePageListCache } from '@/composables/usePageListCache';
import { couponApi } from '@/api/modules';
import { saveCheckoutSession, RUSHING_COUPON_PAY_AMOUNT } from '@/utils/checkout';
import { canReceiveCoupon, getCouponPlazaPhase, resolveCouponHasBought, isCouponUnlimitedStock } from '@/utils/couponPlaza';
import { confirmAction } from '@/utils/confirm';
import { toast } from '@/utils/toast';
import { usePageRefresh } from '@/composables/pullRefresh';
import { useAuthStore } from '@/stores/auth';

const tabs = [
  { label: '全部', value: 'all' },
  { label: '即将开始', value: 'upcoming' },
  { label: '进行中', value: 'ongoing' },
  { label: '已结束', value: 'ended' }
] as const;

const list = ref<any[]>([]);
const keyword = ref('');
const statusTab = ref<(typeof tabs)[number]['value']>('all');
const loading = ref(false);
const finished = ref(false);
const pageNo = ref(0);
const pageTotal = ref(1);
const router = useRouter();
const scrollRoot = ref<HTMLElement>();
const sentinelRef = ref<HTMLElement>();
const rushingCouponId = ref<string | null>(null);
let observer: IntersectionObserver | null = null;

const enrichCoupon = (item: Record<string, any>) => ({
  ...item,
  phase: getCouponPlazaPhase(item),
  hasBought: resolveCouponHasBought(item)
});

const setupObserver = () => {
  observer?.disconnect();
  if (!sentinelRef.value) return;
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) loadMore();
    },
    { root: scrollRoot.value, rootMargin: '100px' }
  );
  observer.observe(sentinelRef.value);
};

const pageCache = usePageListCache({
  cacheKey: () => `/coupons|${statusTab.value}|${keyword.value.trim()}`,
  scrollRef: scrollRoot,
  getState: () => ({
    statusTab: statusTab.value,
    keyword: keyword.value,
    list: list.value,
    pageNo: pageNo.value,
    pageTotal: pageTotal.value,
    finished: finished.value
  }),
  setState: (state) => {
    statusTab.value = state.statusTab as (typeof tabs)[number]['value'];
    keyword.value = String(state.keyword ?? '');
    list.value = (state.list as any[]) || [];
    pageNo.value = Number(state.pageNo) || 0;
    pageTotal.value = Number(state.pageTotal) || 1;
    finished.value = !!state.finished;
    loading.value = false;
  },
  afterRestore: setupObserver
});

const loadMore = async () => {
  if (loading.value || finished.value) return;
  if (pageNo.value >= pageTotal.value && pageNo.value > 0) {
    finished.value = true;
    return;
  }

  loading.value = true;
  try {
    const next = pageNo.value + 1;
    const r = await couponApi.loadDiscountCoupon({
      pageNo: next,
      pageSize: 20,
      status: statusTab.value,
      keyword: keyword.value.trim() || undefined
    });
    const chunk = (r?.list || []).map(enrichCoupon);
    if (next === 1) list.value = chunk;
    else list.value = list.value.concat(chunk);
    pageNo.value = r?.pageNo ?? next;
    pageTotal.value = r?.pageTotal ?? pageNo.value;
    finished.value = pageNo.value >= pageTotal.value;
  } finally {
    loading.value = false;
  }
};

const resetAndLoad = () => {
  pageCache.clear();
  pageNo.value = 0;
  pageTotal.value = 1;
  finished.value = false;
  list.value = [];
  if (scrollRoot.value) scrollRoot.value.scrollTop = 0;
  loadMore();
};

const switchTab = (val: (typeof tabs)[number]['value']) => {
  if (statusTab.value === val) return;
  pageCache.clear();
  statusTab.value = val;
  resetAndLoad();
};

const receive = async (c: any) => {
  if (!canReceiveCoupon(c)) {
    if (resolveCouponHasBought(c)) {
      toast.info('您已购买过该优惠券，不可重复下单');
    }
    return;
  }
  const ok = await confirmAction('确定要抢购该优惠券吗？', {
    title: '抢购确认',
    confirmButtonText: '立即抢购'
  });
  if (!ok) return;
  if (rushingCouponId.value) return;

  rushingCouponId.value = c.couponId;
  try {
    const prepared = await couponApi.rushCoupon(c.couponId);
    if (!prepared?.userCouponId) {
      return;
    }
    if (!prepared?.orderId) {
      toast.warning('下单失败，请重试');
      return;
    }
    saveCheckoutSession(
      [
        {
          productId: c.couponId,
          productName: prepared.couponName || c.couponName || '优惠券',
          productCover: c.cover,
          propertyValueIds: 'coupon_rush',
          propertyValueIdHash: 'coupon_rush',
          propertyData: [{ propertyName: '类型', propertyValue: '优惠券秒杀' }],
          price: Number(prepared.payAmount ?? RUSHING_COUPON_PAY_AMOUNT),
          buyCount: 1
        }
      ],
      2,
      {
        orderId: prepared.orderId,
        payOrderId: prepared.payOrderId,
        payExpireAt: Number(prepared.payExpireAt) || Date.now() + 60_000
      }
    );

    const idx = list.value.findIndex(item => item.couponId === c.couponId);
    if (idx !== -1) {
      list.value[idx] = {
        ...list.value[idx],
        hasBought: true,
        remainCount: isCouponUnlimitedStock(c)
          ? c.remainCount
          : Math.max(0, Number(c.remainCount ?? 1) - 1)
      };
    }
    router.push('/checkout');
  } catch {

  } finally {
    rushingCouponId.value = null;
  }
};

onMounted(async () => {

  await useAuthStore().tryRestoreSession();
  const restored = await pageCache.tryRestore();
  if (!restored) {
    await resetAndLoad();
  }
  setupObserver();
});

usePageRefresh(resetAndLoad, { getScrollEl: () => scrollRoot.value });

onUnmounted(() => observer?.disconnect());
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.coupons-page {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 120px);
  max-height: calc(100vh - 120px);
  background: $color-bg;
  margin: 0;
  padding: 0;
}

.page-header {
  flex-shrink: 0;
  padding: 14px 12px 10px;
  border-radius: $radius-card;
  margin-bottom: 10px;
  border-bottom: none;

  .page-title {
    margin: 0 0 10px;
    font-size: 18px;
    font-weight: 600;
    color: $color-text-title;
    text-align: center;
  }
}

.rush-notice {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 10px;
  padding: 10px 12px;
  border-radius: $radius-btn;
  background: rgba($color-primary, 0.08);
  border: 1px solid rgba($color-primary, 0.18);

  .notice-icon {
    flex-shrink: 0;
    margin-top: 2px;
    font-size: 16px;
    color: $color-primary;
  }

  .notice-text {
    margin: 0;
    font-size: 12px;
    line-height: 1.55;
    color: $color-text-body;

    strong {
      font-weight: 600;
      color: $color-text-title;
    }
  }
}

.search-row {
  .search-input {
    width: 100%;
  }

  .search-icon {
    cursor: pointer;
    color: $color-primary;
  }
}

.status-tabs {
  flex-shrink: 0;
  padding: 0;
  border-radius: $radius-card;
  margin-bottom: 10px;
  border-bottom: none;
  background: #fff;

  .tab-btn {
    flex: 1 0 auto;
    min-width: 72px;
    border: none;
    background: transparent;
    padding: 11px 10px;
    font-size: 13px;
    color: $color-text-body;
    white-space: nowrap;
    cursor: pointer;
    position: relative;

    &.active {
      color: $color-primary;
      font-weight: 600;

      &::after {
        content: '';
        position: absolute;
        left: 50%;
        bottom: 0;
        transform: translateX(-50%);
        width: 28px;
        height: 3px;
        border-radius: $radius-xs;
        background: linear-gradient(90deg, $color-primary, $color-primary-hover);
      }
    }
  }
}

.coupon-list-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px;
  -webkit-overflow-scrolling: touch;
}

.list-empty {
  padding: 40px 0;
}

.load-sentinel {
  height: 1px;
}

.load-tip {
  text-align: center;
  font-size: 12px;
  color: $color-text-muted;
  padding: 12px 0;
}
</style>
