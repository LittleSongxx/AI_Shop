<template>
  <div class="sl-screen-box-wrap ignore">

    <aside class="sl-cat" aria-label="商品分类" @mouseleave="hoverCategory = null">
      <ul class="sl-cat-list">
        <li
          v-for="cat in topCategories"
          :key="cat.categoryId"
          class="sl-cat-item"
          :class="{ active: hoverCategory === cat.categoryId }"
          @mouseenter="hoverCategory = cat.categoryId"
        >
          <button type="button" class="sl-cat-link" @click="goCategory(cat.categoryId)">
            <span class="sl-cat-name">{{ cat.categoryName }}</span>
            <el-icon v-if="cat.children?.length" class="sl-cat-arrow"><ArrowRight /></el-icon>
          </button>
        </li>
      </ul>

      <transition name="sl-flyout">
        <div v-if="activeFlyout" class="sl-cat-flyout">
          <div class="sl-flyout-head">{{ activeFlyout.categoryName }}</div>
          <div class="sl-flyout-body">
            <button
              v-for="sub in activeFlyout.children"
              :key="sub.categoryId"
              type="button"
              class="sl-flyout-sub"
              @click="goCategory(sub.categoryId)"
            >
              {{ sub.categoryName }}
            </button>
            <p v-if="!activeFlyout.children?.length" class="sl-flyout-empty">暂无子分类</p>
          </div>
        </div>
      </transition>
    </aside>

    <div class="sl-stage">
      <div class="sl-banner">
        <button
          v-if="bannerProduct"
          type="button"
          class="sl-banner-btn"
          @click="goDetail(bannerProduct)"
        >
          <ProductImage :product="bannerProduct" fit="cover" width="100%" height="100%" class="sl-banner-bg" />
          <ProductImage :product="bannerProduct" fit="contain" width="100%" height="100%" class="sl-banner-fg" />
          <div class="sl-banner-meta">
            <span class="sl-banner-name">{{ bannerProduct.productName }}</span>
            <span class="sl-banner-price">¥{{ formatPrice(bannerProduct) }}</span>
          </div>
        </button>
        <div v-else class="sl-banner-placeholder">
          <BrandMark class="sl-banner-logo" />
          <p>智选 SmartSelect</p>
        </div>
        <div v-if="bannerList.length > 1" class="sl-banner-dots">
          <button
            v-for="(_, i) in bannerList"
            :key="i"
            type="button"
            :class="{ active: carouselIndex === i }"
            :aria-label="`切换到第 ${i + 1} 张`"
            @click="goBanner(i)"
          />
        </div>
      </div>

      <div class="sl-hot-strip">
        <div class="sl-hot-head">
          <span class="sl-hot-badge">智选</span>
          <span class="sl-hot-title">智选好物</span>
        </div>
        <div class="sl-hot-grid">
          <button
            v-for="p in stripProducts"
            :key="p.productId"
            type="button"
            class="sl-hot-tile"
            @click="goDetail(p)"
          >
            <ProductImage :product="p" fit="contain" width="100%" height="100%" />
            <span class="sl-hot-price">¥{{ formatPrice(p) }}</span>
          </button>
        </div>
      </div>
    </div>

    <aside class="sl-right" aria-label="会员与服务">
      <div class="sl-member-wrap">
        <div class="sl-img-box">
          <el-avatar :size="54" :src="avatarUrl">{{ avatarLetter }}</el-avatar>
        </div>
        <div class="sl-member-info">
          {{ authStore.isLoggedIn ? `Hi，${authStore.userInfo?.nickName || '用户'}` : 'Hi! 欢迎来到智选' }}
        </div>
        <div class="sl-login-and-reg">
          <template v-if="!authStore.isLoggedIn">
            <RouterLink to="/login">登录</RouterLink>
            <RouterLink to="/register">注册</RouterLink>
          </template>
          <template v-else>
            <RouterLink to="/account">个人中心</RouterLink>
            <RouterLink to="/orders">我的订单</RouterLink>
          </template>
        </div>
        <div class="sl-member-ft">
          <RouterLink to="/sign" class="t"><span class="sl-coin">◆</span>签到有礼</RouterLink>
          <RouterLink to="/member-center" class="h"><span class="sl-coin gold">◆</span>会员中心</RouterLink>
        </div>
      </div>

      <button type="button" class="sl-service-banner" @click="openAgent()">
        <el-icon :size="16"><ChatDotRound /></el-icon>
        <span>智能客服 · 在线为你解答</span>
        <el-icon class="arrow"><ArrowRight /></el-icon>
      </button>

      <div class="sl-moudle-wrap">
        <ul>
          <li v-for="item in serviceGrid" :key="item.path">
            <button type="button" @click="onServiceClick(item)">
              <el-icon :size="24"><component :is="item.icon" /></el-icon>
              <p>{{ item.label }}</p>
            </button>
          </li>
        </ul>
      </div>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { RouterLink, useRouter } from 'vue-router';
import {
  ArrowRight,
  ChatDotRound,
  Grid,
  List,
  Location,
  Present,
  Star,
  Ticket,
  Wallet
} from '@element-plus/icons-vue';
import BrandMark from '@/components/common/BrandMark.vue';
import ProductImage from '@/components/common/ProductImage.vue';
import { useOpenAgent } from '@/composables/useOpenAgent';
import { useAuthStore } from '@/stores/auth';
import { resolveAvatarUrl } from '@/utils/image';

const props = defineProps<{
  categories: Array<{
    categoryId: string;
    categoryName: string;
    children?: Array<{ categoryId: string; categoryName: string }>;
  }>;
  hotProducts: any[];
}>();

const router = useRouter();
const { openAgent } = useOpenAgent();
const authStore = useAuthStore();
const carouselIndex = ref(0);
const hoverCategory = ref<string | null>(null);
let carouselTimer: ReturnType<typeof setInterval> | null = null;

const avatarUrl = computed(() => resolveAvatarUrl(authStore.userInfo?.avatar));
const avatarLetter = computed(() => (authStore.userInfo?.nickName || '访')[0]);

const topCategories = computed(() => props.categories.slice(0, 11));

const activeFlyout = computed(
  () => topCategories.value.find((c) => c.categoryId === hoverCategory.value) || null
);

const formatPrice = (p: Record<string, any>) => {
  const val = p?.minPrice ?? p?.price ?? p?.salePrice;
  return val != null ? Number(val).toFixed(2) : '--';
};

const bannerList = computed(() => props.hotProducts.slice(0, 5));
const bannerProduct = computed(() => bannerList.value[carouselIndex.value] || bannerList.value[0]);
const stripProducts = computed(() => props.hotProducts.slice(0, 4));

const startCarousel = () => {
  if (carouselTimer) clearInterval(carouselTimer);
  if (bannerList.value.length <= 1) return;
  carouselTimer = setInterval(() => {
    carouselIndex.value = (carouselIndex.value + 1) % bannerList.value.length;
  }, 4500);
};

const goBanner = (index: number) => {
  const total = bannerList.value.length;
  if (!total) return;
  carouselIndex.value = (index + total) % total;
  startCarousel();
};

watch(
  () => props.hotProducts.length,
  (len) => {
    if (len && carouselIndex.value >= len) carouselIndex.value = 0;
    startCarousel();
  },
  { immediate: true }
);

onUnmounted(() => {
  if (carouselTimer) clearInterval(carouselTimer);
});

const serviceGrid = [
  { label: '全部分类', path: '/search', icon: Grid },
  { label: '优惠券', path: '/coupons', icon: Ticket },
  { label: '我的订单', path: '/orders', icon: List },
  { label: '购物车', path: '/cart', icon: Wallet },
  { label: '签到有礼', path: '/sign', icon: Present },
  { label: '我的收藏', path: '/wishlist', icon: Star },
  { label: '收货地址', path: '/address', icon: Location },
  { label: '消息中心', path: '/notifications', icon: ChatDotRound }
];

const goCategory = (id: string) => {
  if (id) router.push(`/category/${id}`);
  else router.push('/search');
};

const goDetail = (p: any) => {
  if (p?.productId) router.push(`/product/${p.productId}`);
};

const onServiceClick = (item: { path: string }) => {
  if (item.path === '/ai-assistant') openAgent();
  else router.push(item.path);
};
</script>
