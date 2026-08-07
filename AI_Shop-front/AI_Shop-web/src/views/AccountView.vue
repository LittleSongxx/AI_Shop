<template>
  <div class="account-page" :class="{ ignore: isDesktop, 'user-center-layout': isDesktop }">
    <PcUserSidebar v-if="isDesktop" />
    <div :class="isDesktop ? 'user-center-content account-dashboard' : 'account-body'">
    <div v-if="!isDesktop" class="simlect-user-top">
      <div class="simlect-user-head">
        <h1 class="simlect-user-title">个人中心</h1>
        <button
          type="button"
          class="simlect-user-setting"
          aria-label="设置"
          @click="router.push('/account/manage')"
        >
          <el-icon :size="20"><Setting /></el-icon>
        </button>
      </div>

    <section class="profile-card">
      <button type="button" class="profile-main" @click="goProfile">
        <UserAvatar :avatar="user?.avatar" :size="56" />
        <div class="profile-info">
          <div class="nick-row">
            <h2 class="nick">{{ user?.nickName || '智选用户' }}</h2>
            <RouterLink v-if="memberProfile" to="/member-center" class="level-tag" :class="levelTagClass" @click.stop>
              {{ memberProfile.levelName || '普通会员' }}
            </RouterLink>
          </div>
          <p class="account">{{ user?.email || '完善资料享更多权益' }}</p>
          <div v-if="memberProfile" class="exp-bar-wrap" @click.stop="router.push('/member-center')">
            <div class="exp-bar">
              <div class="exp-bar-fill" :style="{ width: growthPercent + '%' }"></div>
            </div>
            <span class="exp-bar-text">{{ memberProfile.growthValue ?? 0 }}/{{ nextLevelGrowth }}</span>
          </div>
        </div>
        <el-icon class="profile-arrow"><ArrowRight /></el-icon>
      </button>
    </section>

    <section class="wallet-strip-top">
      <div class="wallet-item-top" @click="router.push('/orders')">
        <span class="wallet-value-top">{{ totalOrderCount }}</span>
        <span class="wallet-label-top">订单</span>
      </div>
      <div class="wallet-item-top" @click="router.push('/wishlist')">
        <span class="wallet-value-top">{{ wishlistCount }}</span>
        <span class="wallet-label-top">收藏</span>
      </div>
      <div class="wallet-item-top" @click="router.push('/my-coupons')">
        <span class="wallet-value-top">{{ couponCount }}</span>
        <span class="wallet-label-top">优惠券</span>
      </div>
    </section>
    </div>

    <MemberSummaryCard
      v-if="isDesktop && memberProfile"
      :profile="memberProfile"
      :claimable-count="memberClaimableCount"
      :next-level-growth="memberNextLevelGrowth"
      :growth-to-next="memberGrowthToNext"
      class="member-summary--pc"
    />

    <section class="order-card card">
      <div class="card-head">
        <h3>我的订单</h3>
        <RouterLink to="/orders" class="link-more">全部订单</RouterLink>
      </div>
      <div class="order-grid">
        <button
          v-for="item in orderTabs"
          :key="item.code"
          type="button"
          class="order-tab"
          @click="goOrders(item.status)"
        >
          <el-badge :value="countMap[item.code] || 0" :hidden="!countMap[item.code]" :max="99">
            <el-icon :size="26"><component :is="item.icon" /></el-icon>
          </el-badge>
          <span>{{ item.name }}</span>
        </button>
      </div>
    </section>

    <section class="menu-card card">
      <h3 class="menu-title">常用服务</h3>
      <div class="menu-grid">
        <RouterLink v-for="m in menus" :key="m.path" :to="m.path" class="menu-item">
          <el-icon :size="24" class="menu-icon"><component :is="m.icon" /></el-icon>
          <span class="menu-label">{{ m.label }}</span>
        </RouterLink>
      </div>
    </section>

    <section class="discover-card card">
      <div class="discover-tabs toolbar-row">
        <button
          type="button"
          class="discover-tab"
          :class="{ active: discoverTab === 'recommend' }"
          @click="discoverTab = 'recommend'"
        >
          推荐
        </button>
        <button
          type="button"
          class="discover-tab"
          :class="{ active: discoverTab === 'reviews' }"
          @click="switchToReviews"
        >
          我的评价
        </button>
      </div>

      <div v-if="discoverTab === 'recommend'" class="discover-body">
        <div v-if="recommendProducts.length" :class="isDesktop ? 'pc-recommend-grid' : 'recommend-grid'">
          <template v-if="isDesktop">
            <PcProductTile
              v-for="item in recommendProducts"
              :key="`${item.product.productId}-${item.displayIndex}`"
              :product="item.product"
              @click="goProduct"
            />
          </template>
          <template v-else>
            <ProductCard
              v-for="item in recommendProducts"
              :key="`${item.product.productId}-${item.displayIndex}`"
              :product="item.product"
              compact
              @click="goProduct"
            />
          </template>
        </div>
        <el-empty v-else-if="!discoverLoading" description="暂无推荐商品" :image-size="72" />
        <p v-if="discoverLoading" class="discover-tip">加载中…</p>
        <div ref="recommendSentinel" class="feed-sentinel">
          <span v-if="discoverLoading" class="feed-tip">加载中…</span>
          <span v-else-if="recommendProducts.length >= MAX_RECOMMEND" class="feed-tip">已展示全部推荐商品</span>
          <span v-else-if="recommendSourceProducts.length === 0 && !discoverLoading" class="feed-tip">暂无推荐商品</span>
        </div>
      </div>

      <div v-else class="discover-body">
        <div v-if="sortedComments.length" class="review-list" :class="{ 'review-grid': isDesktop }">
          <button
            v-for="c in sortedComments"
            :key="c.orderId"
            type="button"
            class="review-item"
            @click="openCommentDetail(c)"
          >
            <div class="review-head">
              <img v-if="commentCover(c)" :src="commentCover(c)" class="review-cover" alt="" />
              <p class="product-name">
                {{ c.productName || '商品' }}
                <span v-if="c.orderItems && c.orderItems.length > 1" class="more-products-btn" @click.stop="showAllProducts(c)">等{{ c.orderItems.length }}件商品</span>
              </p>
              <el-rate v-if="c.star" :model-value="c.star" disabled size="small" />
            </div>
            <p class="review-text">{{ c.commentContent }}</p>
            <p v-if="c.commentBizReply" class="review-biz-reply">
              <span class="tag">商家回复</span>{{ c.commentBizReply }}
            </p>
            <div v-if="commentThumbImages(c).length" class="review-thumbs">
              <img
                v-for="(img, idx) in commentThumbImages(c)"
                :key="idx"
                :src="toCommentImg(img)"
                alt=""
              />
            </div>
            <p v-if="c.recommentContent" class="review-reply">
              <span class="tag">追评</span>{{ c.recommentContent }}
            </p>
            <p v-if="c.recommentTime || c.commentTime" class="review-time">{{ formatCommentTime(c.recommentTime || c.commentTime) }}</p>
          </button>
        </div>
        <el-empty v-else-if="!discoverLoading" description="暂无评价" :image-size="72" />
        <p v-if="discoverLoading" class="discover-tip">加载中…</p>
        <button
          v-if="sortedComments.length && !commentFinished && !discoverLoading"
          type="button"
          class="load-more-btn"
          @click="loadMoreComments"
        >
          加载更多评价
        </button>
      </div>
    </section>

    <OrderCommentPreviewDialog ref="commentPreviewRef" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import PcUserSidebar from '@/components/layout/PcUserSidebar.vue';
import { useDevice } from '@/composables/useDevice';
import {
  ArrowRight,
  Bell,
  ChatDotRound,
  Location,
  Medal,
  Present,
  Setting,
  Ticket,
  Wallet,
  Box,
  Van,
  Star,
  User,
  Tickets
} from '@element-plus/icons-vue';
import UserAvatar from '@/components/common/UserAvatar.vue';
import MemberSummaryCard from '@/components/account/MemberSummaryCard.vue';
import ProductCard from '@/components/business/ProductCard.vue';
import PcProductTile from '@/components/pc/PcProductTile.vue';
import OrderCommentPreviewDialog from '@/components/business/OrderCommentPreviewDialog.vue';
import { resolveImageUrl, splitImagePaths } from '@/utils/image';
import { accountApi, commentApi, couponApi, favoriteApi, orderApi, productApi } from '@/api/modules';
import { useAuthStore } from '@/stores/auth';
import { filterOnSaleProducts } from '@/utils/product';
import { usePageRefresh } from '@/composables/pullRefresh';
import { ElMessageBox } from 'element-plus';

const router = useRouter();
const { isDesktop } = useDevice();
const authStore = useAuthStore();
const user = ref<Record<string, any>>({});
const countMap = reactive<Record<string, number>>({});
const memberProfile = ref<Record<string, any> | null>(null);
const memberClaimableCount = ref(0);
const memberNextLevelGrowth = ref<number | null>(null);
const memberGrowthToNext = ref<number | null>(null);
const wishlistCount = ref(0);
const couponCount = ref(0);

const discoverTab = ref<'recommend' | 'reviews'>('recommend');
const discoverLoading = ref(false);
const recommendProducts = ref<any[]>([]);
const recommendSourceProducts = ref<any[]>([]);
const recommendDisplayCount = ref(0);
const MAX_RECOMMEND = 90;
const recommendSentinel = ref<HTMLElement | null>(null);
let recommendObserver: IntersectionObserver | null = null;
const myComments = ref<any[]>([]);
const commentPageNo = ref(0);
const commentPageTotal = ref(1);
const commentFinished = ref(false);
const commentPreviewRef = ref<InstanceType<typeof OrderCommentPreviewDialog>>();

const splitCommentImages = (val: unknown) => splitImagePaths(val as string | null);

const toCommentImg = (path: string) => resolveImageUrl(path, { useThumbnail: true }) || path;

const commentCover = (c: Record<string, unknown>) => {
  const cover = c.cover as string | undefined;
  return cover ? resolveImageUrl(cover, { useThumbnail: true }) : '';
};

const commentThumbImages = (c: Record<string, unknown>) => {
  const all = [...splitCommentImages(c.commentImages), ...splitCommentImages(c.recommentImages)];
  return all.slice(0, 3);
};

const formatCommentTime = (val: unknown) => {
  if (!val) return '';
  if (typeof val === 'string') return val.replace('T', ' ').slice(0, 19);
  const d = new Date(val as string | number);
  if (Number.isNaN(d.getTime())) return String(val);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

const commentSortTime = (c: Record<string, unknown>) => {
  const t = (c.recommentTime as string) || (c.commentTime as string);
  return t ? new Date(t).getTime() : 0;
};

const sortedComments = computed(() =>
  [...myComments.value].sort((a, b) => commentSortTime(b) - commentSortTime(a))
);

const totalOrderCount = computed(() => countMap['completed'] || 0);

const levelTagClass = computed(() => {
  const code = Number(memberProfile.value?.levelCode ?? 1);
  if (code >= 3) return 'level-gold';
  if (code >= 2) return 'level-silver';
  return 'level-default';
});

const nextLevelGrowth = computed(() => {
  const current = memberProfile.value?.growthValue ?? 0;
  const toNext = memberGrowthToNext.value ?? 0;
  return current + toNext;
});

const growthPercent = computed(() => {
  const current = memberProfile.value?.growthValue ?? 0;
  const total = nextLevelGrowth.value;
  if (total <= 0) return 0;
  return Math.min(Math.round((current / total) * 100), 100);
});

const openCommentDetail = (c: Record<string, unknown>) => {
  commentPreviewRef.value?.show(c);
};

const showAllProducts = (row: Record<string, any>) => {
  const items = row.orderItems || []
  let html = '<div style="max-height:400px;overflow-y:auto;">'
  items.forEach((item: Record<string, any>, idx: number) => {
    const cover = item.cover ? `<img src="${resolveImageUrl(item.cover)}" style="width:60px;height:60px;object-fit:cover;border-radius:6px;flex-shrink:0;" />` : ''
    html += `<div style="display:flex;gap:12px;padding:10px 0;${idx > 0 ? 'border-top:1px solid #eee;' : ''}">
      ${cover}
      <div style="flex:1;min-width:0;">
        <div style="font-size:14px;font-weight:500;margin-bottom:4px;color:#333333;">${item.productName || ''}</div>
        <div style="font-size:12px;color:#86868b;">${item.propertyInfo || ''}</div>
        <div style="font-size:12px;color:#86868b;margin-top:2px;">￥${item.itemAmount || 0} × ${item.buyCount || 0}</div>
      </div>
    </div>`
  })
  html += '</div>'
  try {
    ElMessageBox.alert(html, '该订单商品', {
      dangerouslyUseHTMLString: true,
      confirmButtonText: '关闭',
      showCancelButton: false,
      closeOnClickModal: true,
    })
  } catch (e) {
    console.error(e)
  }
}

const orderTabs = [
  { code: 'pendingPayment', name: '待付款', status: '0', icon: Wallet },
  { code: 'pendingShipment', name: '待发货', status: '1', icon: Box },
  { code: 'pendingReceipt', name: '待收货', status: '2', icon: Van },
  { code: 'pendingComment', name: '待评价', status: '8', icon: Star },
  { code: 'afterSale', name: '售后', status: 'all', icon: ChatDotRound }
];

const menus = [
  { label: '会员中心', path: '/member-center', icon: Medal },
  { label: '消息中心', path: '/notifications', icon: Bell },
  { label: '支付记录', path: '/pay-records', icon: Wallet },
  { label: '收货地址', path: '/address', icon: Location },
  { label: '优惠券', path: '/my-coupons', icon: Ticket },
  { label: '收藏', path: '/wishlist', icon: Star },
  { label: '足迹', path: '/footprint', icon: Box },
  { label: '签到中心', path: '/sign', icon: Present },
  { label: '智能客服', path: '/ai-assistant', icon: ChatDotRound },
  { label: '购物偏好', path: '/shopping-profile', icon: User },
  { label: '我的工单', path: '/support-cases', icon: Tickets }
];

const loadMember = async () => {
  try {
    const center: any = await authStore.loadMemberCenter();
    if (!center) return;
    memberProfile.value = center?.profile ?? null;
    const rewards = center?.rewards || [];
    memberClaimableCount.value = rewards.filter((r: { claimable?: boolean }) => r.claimable).length;
    memberNextLevelGrowth.value = center?.nextLevelGrowth ?? null;
    memberGrowthToNext.value = center?.growthToNext ?? null;
  } catch {
    memberProfile.value = null;
    memberClaimableCount.value = 0;
    memberNextLevelGrowth.value = null;
    memberGrowthToNext.value = null;
  }
};

const load = async () => {
  user.value = (await accountApi.getUserInfo()) || authStore.userInfo || {};
  authStore.userInfo = { ...authStore.userInfo, ...user.value };
  const counts = await orderApi.getOrderCountInfo();
  if (Array.isArray(counts)) {
    counts.forEach((item: { code: string; count: number }) => {
      countMap[item.code] = item.count ?? 0;
    });
  }
  if (authStore.isLoggedIn) {
    await loadMember();

    try {
      const [favRes, couponRes] = await Promise.all([
        favoriteApi.loadFavorite({ pageNo: 1 }),
        couponApi.loadUserCoupon({ pageNo: 1, status: 0 })
      ]);
      wishlistCount.value = favRes?.list?.length ?? 0;
      couponCount.value = couponRes?.list?.length ?? 0;
    } catch {
      wishlistCount.value = 0;
      couponCount.value = 0;
    }
  }
};

const loadRecommend = async () => {
  discoverLoading.value = true;
  try {
    const commend = await productApi.loadCommendProduct();
    const list = Array.isArray(commend) ? commend : commend?.list;
    recommendSourceProducts.value = filterOnSaleProducts(list);

    const initialCount = isDesktop.value ? 12 : 8;
    recommendDisplayCount.value = Math.min(initialCount, MAX_RECOMMEND);
    updateRecommendDisplay();
  } finally {
    discoverLoading.value = false;
  }
};

const updateRecommendDisplay = () => {
  if (recommendSourceProducts.value.length === 0) {
    recommendProducts.value = [];
    return;
  }

  const result: { product: any; displayIndex: number }[] = [];
  const sourceLength = recommendSourceProducts.value.length;

  for (let i = 0; i < recommendDisplayCount.value; i++) {
    const sourceIndex = i % sourceLength;
    result.push({ product: recommendSourceProducts.value[sourceIndex], displayIndex: i });
  }

  recommendProducts.value = result;
};

const loadMoreRecommend = () => {
  if (recommendDisplayCount.value >= MAX_RECOMMEND) return;

  const increment = isDesktop.value ? 12 : 8;
  recommendDisplayCount.value = Math.min(recommendDisplayCount.value + increment, MAX_RECOMMEND);
  updateRecommendDisplay();
};

const setupRecommendObserver = () => {
  if (recommendObserver) return;

  recommendObserver = new IntersectionObserver(
    (entries) => {
      const entry = entries[0];
      if (entry.isIntersecting && !discoverLoading.value && recommendDisplayCount.value < MAX_RECOMMEND) {
        loadMoreRecommend();
      }
    },
    {
      rootMargin: '200px',
      threshold: 0.1
    }
  );

  if (recommendSentinel.value) {
    recommendObserver.observe(recommendSentinel.value);
  }
};

const cleanupRecommendObserver = () => {
  if (recommendObserver) {
    recommendObserver.disconnect();
    recommendObserver = null;
  }
};

const loadComments = async (reset = false) => {
  if (reset) {
    commentPageNo.value = 0;
    commentPageTotal.value = 1;
    commentFinished.value = false;
    myComments.value = [];
  }
  if (commentFinished.value) return;
  discoverLoading.value = true;
  try {
    const next = commentPageNo.value + 1;
    const r = await commentApi.loadMyComment({ pageNo: next });
    const chunk = r?.list || [];
    if (next === 1) myComments.value = chunk;
    else myComments.value = myComments.value.concat(chunk);
    commentPageNo.value = r?.pageNo ?? next;
    commentPageTotal.value = r?.pageTotal ?? commentPageNo.value;
    commentFinished.value = commentPageNo.value >= commentPageTotal.value;
  } finally {
    discoverLoading.value = false;
  }
};

const switchToReviews = () => {
  discoverTab.value = 'reviews';
  if (!myComments.value.length && !commentFinished.value) {
    loadComments(true);
  }
};

const loadMoreComments = () => loadComments(false);

const goProfile = () => router.push('/account/profile');
const goProduct = (p: any) => {
  if (p?.productId) router.push(`/product/${p.productId}`);
};

const goOrders = (status: string) => {
  if (status === 'all') {
    router.push('/after-sale');
    return;
  }
  router.push({ path: '/orders', query: { status } });
};

const refreshPage = async () => {
  await load();
  if (authStore.isLoggedIn) await loadMember();
  if (discoverTab.value === 'reviews') {
    await loadComments(true);
  } else {
    await loadRecommend();
  }
};

onMounted(async () => {
  await load();
  await loadRecommend();

  setTimeout(() => {
    setupRecommendObserver();
  }, 100);
});

onUnmounted(() => {
  cleanupRecommendObserver();
});
usePageRefresh(refreshPage);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.account-page {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0 (-$app-page-gutter);
  padding: 0 0 16px;
  background: linear-gradient(180deg, #FFFFFF 0%, $color-bg 120px);

  &.user-center-layout {
    flex-direction: row;
    align-items: flex-start;
    margin: 0;
    padding: 0;
    gap: 16px;
    background: transparent;
  }

  :deep(.member-summary--pc) {
    margin: 0 0 12px;
  }
}

.profile-card {
  margin: 0 $app-page-gutter;
  padding: 0 4px 12px;
}

.profile-main {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
  -webkit-tap-highlight-color: transparent;

  &:active {
    opacity: 0.92;
  }
}

.profile-info {
  flex: 1;
  min-width: 0;

  .nick-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 2px;
  }

  .nick {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
    color: $color-text-title;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .level-tag {
    flex-shrink: 0;
    padding: 1px 8px;
    border-radius: $radius-pill;
    font-size: 10px;
    font-weight: 600;
    text-decoration: none;
    line-height: 1.6;

    &.level-default {
      background: rgba(255, 255, 255, 0.15);
      color: rgba(255, 255, 255, 0.85);
      border: 1px solid rgba(255, 255, 255, 0.2);
    }

    &.level-silver {
      background: linear-gradient(135deg, #d4d4d8, #a8a8ad);
      color: #fff;
      border: 1px solid rgba(168, 168, 173, 0.45);
    }

    &.level-gold {
      background: linear-gradient(135deg, #e8c96a, #c9a962);
      color: #fff;
      border: 1px solid rgba(201, 169, 98, 0.45);
    }
  }

  .account {
    margin: 0;
    font-size: 12px;
    color: $color-text-muted;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.exp-bar-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  cursor: pointer;

  .exp-bar {
    flex: 1;
    height: 6px;
    background: rgba(255, 255, 255, 0.15);
    border-radius: 3px;
    overflow: hidden;
    min-width: 60px;
  }

  .exp-bar-fill {
    height: 100%;
    background: linear-gradient(90deg, #ffd700, #c9a962);
    border-radius: 3px;
    transition: width 0.6s ease;
  }

  .exp-bar-text {
    font-size: 10px;
    color: rgba(255, 255, 255, 0.55);
    white-space: nowrap;
    line-height: 1;
  }
}

.profile-arrow {
  flex-shrink: 0;
  font-size: 16px;
  color: $color-text-disabled;
}

.wallet-strip-top {
  display: flex;
  align-items: center;
  margin: 8px $app-page-gutter 0;
  padding: 14px 0 4px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.wallet-item-top {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  cursor: pointer;

  &:active {
    opacity: 0.8;
  }
}

.wallet-value-top {
  font-size: 18px;
  font-weight: 700;
  color: #fff;
  line-height: 1.3;
}

.wallet-label-top {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.6);
  line-height: 1.2;
}

.order-card,
.menu-card,
.discover-card {
  margin: 0 $app-page-gutter;
  overflow: hidden;
}

.order-card {
  padding: 14px 16px 16px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;

  h3 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
  }

  .link-more {
    font-size: 13px;
    color: $color-text-muted;
    text-decoration: none;

    &:hover {
      color: $color-primary;
    }
  }
}

.order-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 4px;
}

.order-tab {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 10px 4px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: $color-text-body;
  font-size: 12px;
  border-radius: $radius-btn;
  transition: background $transition-fast, color $transition-fast, transform $transition-fast;

  &:hover {
    background: $color-primary-muted;
    color: $color-primary;
  }

  &:active {
    transform: scale(0.96);
  }
}

.menu-card {
  padding: 4px 0;
}

.menu-title {
  margin: 0;
  padding: 14px 16px 8px;
  font-size: 16px;
  font-weight: 600;
}

.menu-grid {
  display: flex;
  flex-direction: column;
  gap: 0;
  padding: 4px 0 8px;
}

.menu-item {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  text-decoration: none;
  color: $color-text-title;
  font-size: 13px;
  border-radius: $radius-btn;
  transition: background $transition-fast;

  &:hover {
    background: rgba($color-primary, 0.04);
    color: $color-primary;

    .menu-icon {
      color: $color-primary;
    }
  }

  .menu-label {
    line-height: 1.2;
    text-align: center;
  }

  .menu-icon {
    color: $color-text-muted;
  }
}

.discover-card {
  padding: 0 0 12px;
}

.discover-tabs {
  padding: 0 12px;
  border-bottom: 1px solid $color-border;
}

.discover-tab {
  flex: 1;
  padding: 12px 8px;
  border: none;
  background: transparent;
  font-size: 14px;
  color: $color-text-body;
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

.discover-body {
  padding: 12px 12px 4px;
  min-height: 120px;
}

.feed-sentinel {
  padding: 16px 0;
  text-align: center;
}

.feed-tip {
  margin: 0;
  font-size: 12px;
  color: $color-text-muted;
}

.recommend-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

.review-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.review-item {
  display: block;
  width: 100%;
  padding: 12px 4px;
  border: none;
  border-bottom: 1px solid $color-border;
  background: $color-bg;
  text-align: left;
  cursor: pointer;
  position: relative;
  z-index: 1;

  &:last-child {
    border-bottom: none;
  }

  &:active {
    background: rgba($color-primary, 0.04);
  }

  .review-cover,
  .review-thumbs img {
    filter: none !important;
    opacity: 1 !important;
  }

  .product-name,
  .review-text {
    color: $color-text-title !important;
  }
}

.review-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;

  .review-cover {
    width: 40px;
    height: 40px;
    border-radius: $radius-xs;
    object-fit: cover;
    flex-shrink: 0;
    background: $color-bg-subtle;
  }

  .product-name {
    margin: 0;
    font-size: 13px;
    font-weight: 600;
    color: $color-text-title;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    .more-products-btn {
      display: inline;
      margin-left: 4px;
      padding: 1px 5px;
      border-radius: $radius-xs;
      background: $color-warning-soft;
      color: $color-warning;
      font-size: 10px;
      font-weight: 500;
      cursor: pointer;
      white-space: nowrap;
    }
  }
}

.review-thumbs {
  display: flex;
  gap: 6px;
  margin-bottom: 6px;

  img {
    width: 56px;
    height: 56px;
    object-fit: cover;
    border-radius: $radius-xs;
    background: $color-bg-subtle;
  }
}

.review-text {
  margin: 0 0 6px;
  font-size: 13px;
  line-height: 1.5;
  color: $color-text-body;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.review-biz-reply,
.review-reply {
  margin: 0 0 6px;
  font-size: 12px;
  line-height: 1.45;
  color: $color-text-muted;

  .tag {
    display: inline-block;
    margin-right: 6px;
    padding: 0 6px;
    border-radius: $radius-xs;
    background: $color-primary-muted;
    color: $color-primary;
    font-size: 11px;
  }
}

.review-biz-reply {
  padding: 8px 10px;
  border-radius: $radius-xs;
  background: $color-bg-subtle;
  color: $color-text-body;
}

.review-time {
  margin: 0;
  font-size: 11px;
  color: $color-text-disabled;
}

.discover-tip {
  margin: 0;
  text-align: center;
  font-size: 12px;
  color: $color-text-muted;
  padding: 16px 0;
}

.load-more-btn {
  display: block;
  width: 100%;
  margin-top: 8px;
  padding: 10px;
  border: none;
  background: transparent;
  font-size: 13px;
  color: $color-primary;
  cursor: pointer;
}

@media (min-width: $breakpoint-tablet) {
  .recommend-grid {
    grid-template-columns: repeat(6, 1fr);
    gap: 10px;
  }
}
</style>
