<template>
  <div class="home-page ignore">

    <div ref="homeScrollRef" class="simlect-home-scroll">

      <HomeFeatureCards />

      <AIGuideCard />

      <section class="simlect-menus card">
        <button v-for="item in menuItems" :key="item.path" type="button" class="simlect-menu-item" @click="router.push(item.path)">
          <span class="simlect-menu-icon" :style="{ color: menuIconStyle.color }"><el-icon :size="28"><component :is="item.icon" /></el-icon></span>
          <span class="simlect-menu-label">{{ item.label }}</span>
        </button>
      </section>

      <section v-if="editorPicks.length >= 2" class="editor-picks card">
        <header class="editor-head">
          <div>
            <h3 class="editor-title">编辑精选</h3>
            <p class="editor-sub">买手团队严选，品质之选</p>
          </div>
          <button type="button" class="editor-more" @click="router.push('/recommend')">
            更多 <el-icon :size="14"><ArrowRight /></el-icon>
          </button>
        </header>
        <div class="editor-grid">
          <button
            v-for="item in editorPicks"
            :key="item.productId"
            type="button"
            class="editor-item"
            @click="goDetail(item)"
          >
            <div class="editor-img-wrap">
              <ProductImage :product="item" fit="cover" width="100%" height="100%" class="editor-img" :lazy="false" />
              <span class="editor-badge">甄选</span>
            </div>
            <div class="editor-meta">
              <h4 class="editor-name">{{ item.productName }}</h4>
              <p class="editor-price">¥{{ formatPrice(item.price) }}</p>
            </div>
          </button>
        </div>
      </section>

      <section id="recommend-section" class="simlect-feed">
        <header class="feed-head">
          <span class="feed-title">为你推荐</span>
          <span class="feed-sub">每日上新 · 品质精选</span>
        </header>
        <el-skeleton :loading="loading" animated :count="1">
          <template #template>
            <div class="simlect-waterfall">
              <el-skeleton-item v-for="n in 6" :key="n" variant="image" style="height: 200px" />
            </div>
          </template>
          <div v-if="displayProducts.length" class="simlect-waterfall">
            <ProductCard
              v-for="item in displayProducts"
              :key="`${item.product.productId}-${item.displayIndex}`"
              :product="item.product"
              compact
              :image-lazy="item.displayIndex >= 8"
              @click="goDetail"
            />
          </div>
          <el-empty v-else description="暂无商品，稍后再来看看" />
        </el-skeleton>
        <div ref="feedSentinel" class="feed-sentinel">
          <span v-if="feedLoading" class="feed-tip">加载中…</span>
          <p v-else-if="feedLoadError" class="feed-error">
            <button type="button" class="feed-retry" @click="retryFeed">加载失败，点击重试</button>
          </p>
          <span v-else-if="feedFinished && displayProducts.length >= MAX_PRODUCTS" class="feed-tip">已展示全部推荐商品</span>
          <span v-else-if="feedFinished && displayProducts.length" class="feed-tip">没有更多了</span>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import {
  ArrowRight,
  ChatDotRound,
  Grid,
  List,
  Present,
  Ticket
} from '@element-plus/icons-vue';
import ProductCard from '@/components/business/ProductCard.vue';
import ProductImage from '@/components/common/ProductImage.vue';
import AIGuideCard from '@/components/business/AIGuideCard.vue';
import HomeFeatureCards from '@/components/business/HomeFeatureCards.vue';
import { productApi } from '@/api/modules';
import { usePageRefresh } from '@/composables/pullRefresh';
import { isStandaloneDisplay } from '@/utils/standalone';
import { filterOnSaleProducts } from '@/utils/product';
import { isFeatureSupported } from '@/integrations/featureRegistry';
import { useAuthStore } from '@/stores/auth';
import {
  applyScroll,
  getScrollForPath,
  restoreScrollForPath,
  saveScrollForPath
} from '@/utils/scrollMemory';
import {
  clearHomeBootstrap,
  prefetchHomeBootstrap,
  signalHomeSplashPaintReady,
  takeHomeBootstrap,
  waitForHomeVisibleImagesInDom
} from '@/utils/homeBootstrap';

const router = useRouter();
const authStore = useAuthStore();
const loading = ref(true);
const feedLoadError = ref(false);
const products = ref<any[]>([]);
const hotProductsList = ref<any[]>([]);
const homeScrollRef = ref<HTMLElement | null>(null);

const menuIconStyle = computed(() => {
  const level = authStore.memberLevelCode;
  if (level >= 3) {
    return { color: '#B8860B' };
  }
  if (level >= 2) {
    return { color: '#757575' };
  }
  return { color: '#0F766E' };
});

const feedSentinel = ref<HTMLElement | null>(null);
const feedPageNo = ref(0);
const feedPageTotal = ref(1);
const feedLoading = ref(false);
const feedFinished = ref(false);
const MAX_PRODUCTS = 120;
let feedObserver: IntersectionObserver | null = null;

const menuItems = computed(() => {

  const all = [
    { label: '全部分类', icon: Grid, path: '/search', feature: 'category_tree' as const },
    { label: '优惠券', icon: Ticket, path: '/coupons', feature: 'coupon_plaza' as const },
    { label: '我的订单', icon: List, path: '/orders', feature: 'order_list' as const },
    { label: '签到有礼', icon: Present, path: '/sign', feature: 'sign_in' as const },
    { label: '智能客服', icon: ChatDotRound, path: '/ai-assistant', feature: 'agent_chat' as const }
  ];
  return all.filter((m) => isFeatureSupported(m.feature));
});

const editorPicks = computed(() => hotProductsList.value.slice(0, 4));

const formatPrice = (price: any): string => {
  const n = Number(price);
  if (isNaN(n)) return '--';
  return n.toFixed(2);
};

const displayProducts = computed(() => {
  return products.value
    .slice(0, MAX_PRODUCTS)
    .map((product, displayIndex) => ({ product, displayIndex }));
});

const loadFeed = async (reset = false) => {
  if (feedLoading.value) return;

  if (reset) {
    feedPageNo.value = 0;
    feedPageTotal.value = 1;
    feedFinished.value = false;
    products.value = [];
  }

  if (feedFinished.value) return;

  feedLoading.value = true;
  feedLoadError.value = false;
  try {
    const next = feedPageNo.value + 1;
    const page = await productApi.loadProduct({ pageNo: next });
    const chunk = filterOnSaleProducts(page?.list);

    if (chunk.length > 0) {
      const existingIds = new Set(products.value.map(p => p.productId));
      const filtered = chunk.filter(p => !existingIds.has(p.productId));
      products.value = products.value.concat(filtered);
    }

    feedPageNo.value = Number(page?.pageNo) || next;
    feedPageTotal.value = Number(page?.pageTotal) || feedPageNo.value;

    const hasMoreData = chunk.length > 0 && feedPageNo.value < feedPageTotal.value;

    if (!hasMoreData) {
      feedFinished.value = true;
    }
  } catch (error) {
    console.error('HomeView: loadFeed error', error);
    feedLoadError.value = true;
  } finally {
    feedLoading.value = false;
  }
};

const retryFeed = () => {
  void loadFeed(products.value.length === 0);
};

const resetHomeScroll = () => {
  saveScrollForPath('/', { top: 0, target: 'window' });
  applyScroll({ top: 0, target: 'window' });
  if (homeScrollRef.value) homeScrollRef.value.scrollTop = 0;
};

const load = async (opts?: { prefetch?: boolean; fromPullRefresh?: boolean }) => {
  loading.value = true;
  try {
    const commend = await productApi.loadCommendProduct();
    hotProductsList.value = filterOnSaleProducts(Array.isArray(commend) ? commend : commend?.list);
    await loadFeed(true);

    if (opts?.prefetch) {
      while (!feedFinished.value && products.value.length < MAX_PRODUCTS) {
        await loadFeed();
      }
    }
  } finally {
    loading.value = false;
    if (opts?.fromPullRefresh) {
      await nextTick();
      resetHomeScroll();
      requestAnimationFrame(resetHomeScroll);
    }
  }
};

const goDetail = (p: any) => {
  if (p?.productId) router.push(`/product/${p.productId}`);
};

onMounted(async () => {
  const savedScroll = getScrollForPath('/');
  const shouldPrefetch = !savedScroll || savedScroll.top > 0;
  let bootstrap = takeHomeBootstrap();
  if (!bootstrap) {
    try {
      bootstrap = await prefetchHomeBootstrap(shouldPrefetch);
    } catch {
      bootstrap = null;
    }
  }

  if (bootstrap) {
    hotProductsList.value = bootstrap.hotProducts;
    products.value = bootstrap.products;
    feedPageNo.value = bootstrap.feedPageNo;
    feedPageTotal.value = bootstrap.feedPageTotal;
    feedFinished.value = bootstrap.feedFinished;
    loading.value = false;
    if (shouldPrefetch && !bootstrap.feedFinished) {
      void (async () => {
        while (!feedFinished.value && products.value.length < MAX_PRODUCTS) {
          await loadFeed();
        }
      })();
    }
  } else {
    await Promise.all([
      authStore.loadMemberLevel(),
      load({ prefetch: shouldPrefetch })
    ]);
  }

  if (savedScroll && savedScroll.top > 0) {
    await nextTick();
    restoreScrollForPath('/');
  }

  feedObserver = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) loadFeed();
    },
    { rootMargin: '400px 0px' }
  );
  if (feedSentinel.value) feedObserver.observe(feedSentinel.value);

  if (isStandaloneDisplay()) {
    await waitForHomeVisibleImagesInDom(homeScrollRef.value);
    signalHomeSplashPaintReady();
  }
});

onUnmounted(() => {
  feedObserver?.disconnect();
  feedObserver = null;
});

usePageRefresh(() => {
  clearHomeBootstrap();
  return load({ prefetch: false, fromPullRefresh: true });
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.home-page.ignore {
  background: transparent;
}

.pc-home-placeholder {
  padding: 80px 16px;
  text-align: center;
}

.simlect-home-scroll {
  padding-bottom: $mobile-tab-reserved;
}

.simlect-menus {
  margin: 10px $app-page-gutter 0;
  padding: 14px 8px 8px;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 6px 4px;
}

.simlect-menu-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 2px 0 6px;
  border: none;
  background: transparent;
  cursor: pointer;
}

.simlect-menu-icon {
  display: grid;
  place-items: center;
  line-height: 1;
  transition: transform 0.2s ease;
}

.simlect-menu-item:hover .simlect-menu-icon {
  transform: translateY(-2px);
}

.simlect-menu-item:active .simlect-menu-icon {
  transform: scale(0.95);
}

.simlect-menu-label {
  font-size: 11px;
  color: $color-text-body;
  line-height: 1.2;
  text-align: center;
}

.simlect-sort {
  margin: 10px $app-page-gutter 0;
  padding: 16px 12px;
  border-radius: 8px;
}

.simlect-sort-scroll {
  display: flex;
  gap: 16px;
  overflow-x: auto;
  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }
}

.simlect-sort-item {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  border: none;
  background: transparent;
  cursor: pointer;
  padding: 0;

  &.active .simlect-sort-avatar {
    border-color: $color-gold;
    color: $color-gold;
  }

  &.active .simlect-sort-name {
    color: $color-primary;
    font-weight: 600;
  }
}

.simlect-sort-avatar {
  width: 45px;
  height: 45px;
  border-radius: 50%;
  border: 2px solid $color-border;
  display: grid;
  place-items: center;
  font-size: 16px;
  font-weight: 600;
  color: $color-text-body;
  background: $color-card;
}

.simlect-sort-name {
  font-size: 12px;
  color: $color-text-body;
  max-width: 56px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.editor-picks {
  margin: 12px $app-page-gutter 0;
  padding: 16px 12px 14px;
}

.editor-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 14px;

  .editor-title {
    font-size: 16px;
    font-weight: 600;
    color: $color-text-title;
    margin: 0;
    line-height: 1.3;
  }

  .editor-sub {
    font-size: 12px;
    color: $color-text-muted;
    margin: 2px 0 0;
  }
}

.editor-more {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 0;
  border: none;
  background: transparent;
  font-size: 12px;
  color: $color-text-muted;
  cursor: pointer;
}

.editor-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.editor-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: transform 0.2s ease;

  &:active {
    transform: scale(0.97);
  }
}

.editor-img-wrap {
  position: relative;
  width: 100%;
  aspect-ratio: 1 / 1;
  border-radius: $radius-sm;
  overflow: hidden;
  background: $color-bg-subtle;
}

.editor-img {
  :deep(.product-image),
  :deep(.el-image) {
    width: 100% !important;
    height: 100% !important;
  }
}

.editor-badge {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 2px 8px;
  border-radius: $radius-tag;
  background: rgba(29, 29, 31, 0.75);
  backdrop-filter: blur(8px);
  color: $color-gold;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0;
}

.editor-meta {
  .editor-name {
    margin: 0;
    font-size: 13px;
    font-weight: 500;
    color: $color-text-title;
    line-height: 1.3;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .editor-price {
    margin: 4px 0 0;
    font-size: 14px;
    font-weight: 600;
    color: $color-price;
  }
}

.simlect-feed {
  margin: 14px $app-page-gutter 12px;
}

.feed-head {
  margin-bottom: 16px;
  padding-left: 4px;

  .feed-title {
    font-size: 16px;
    font-weight: 600;
    color: $color-text-title;
    line-height: 1.3;
    padding-right: 8px;
  }

  .feed-sub {
    font-size: 12px;
    color: $color-text-muted;
    margin: 0;
  }
}

.simlect-waterfall {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.feed-sentinel {
  padding: 12px 0 16px;
  text-align: center;
}

.feed-error {
  margin: 0;
  padding: 24px 0;
  text-align: center;
}

.feed-retry {
  border: none;
  background: none;
  padding: 0;
  font-size: 14px;
  color: $color-primary;
  cursor: pointer;
  text-decoration: underline;
}

.feed-tip {
  font-size: 12px;
  color: $color-text-muted;
}
</style>
