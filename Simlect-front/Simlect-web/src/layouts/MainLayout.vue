<template>
  <div class="layout" :class="layoutClasses">
    <HomeSearchHeader v-if="isHomeTab" />
    <NotificationPopup @click="handleNotificationClick" />

    <TabPageHeader v-if="tabHeader" :title="tabHeader.title">
      <template v-if="route.path === '/search'" #right>
        <div class="tab-header-actions">
          <AgentServiceEntry />
          <el-icon class="header-action" :size="22" @click="openSearchPortal"><Search /></el-icon>
        </div>
      </template>
      <template v-else-if="route.path === '/account'" #right>
        <div class="tab-header-actions">
          <AgentServiceEntry />
          <button
            type="button"
            class="header-action header-action-btn"
            aria-label="消息"
            @click="router.push('/notifications')"
          >
            <el-badge :value="unreadCount" :hidden="!unreadCount" :max="99">
              <el-icon :size="22"><Bell /></el-icon>
            </el-badge>
          </button>
          <el-icon
            class="header-action"
            :size="22"
            aria-label="设置"
            @click="router.push('/account/manage')"
          >
            <Setting />
          </el-icon>
        </div>
      </template>
      <template v-else-if="route.path === '/cart'" #right>
        <AgentServiceEntry />
      </template>
    </TabPageHeader>

    <header v-if="showSiteHeader" class="site-header">
      <div class="header-inner">
        <RouterLink class="brand" to="/">
          <BrandMark class="brand-icon" />
          <span class="brand-text">简选</span>
        </RouterLink>

        <div class="mobile-tools">
          <el-icon class="tool-btn" :size="22" @click="openSearchPortal"><Search /></el-icon>
          <el-badge :value="cartStore.cartCount" :hidden="!cartStore.cartCount" :max="99">
            <el-icon class="tool-btn" :size="22" @click="router.push('/cart')"><ShoppingCart /></el-icon>
          </el-badge>
          <el-icon class="tool-btn" :size="22" @click="mobileMenuOpen = true"><Menu /></el-icon>
        </div>

        <div class="search-box">
          <el-select
            v-model="searchCategoryId"
            placeholder="分类"
            class="search-category"
            popper-class="search-category-popper"
          >
            <el-option label="全部商品" value="" />
            <el-option
              v-for="c in categoryList"
              :key="c.categoryId"
              :label="c.categoryName"
              :value="c.categoryId"
            />
          </el-select>
          <div class="search-divider" />
          <input
            v-model="keyword"
            class="search-input"
            type="search"
            placeholder="搜索简选"
            @keyup.enter="goSearch"
          />
          <button type="button" class="search-submit" @click="goSearch">
            <el-icon><Search /></el-icon>
            <span>搜索</span>
          </button>
        </div>

        <nav class="header-actions">
          <template v-if="!authStore.isLoggedIn">
            <RouterLink class="action-link" to="/login">请登录</RouterLink>
            <RouterLink class="action-link highlight" to="/register">免费注册</RouterLink>
          </template>
          <el-dropdown v-else trigger="click" popper-class="user-dropdown-popper">
            <button type="button" class="user-trigger">
              <el-avatar :size="28" :src="avatarUrl" class="user-avatar">
                {{ (authStore.userInfo?.nickName || '用')[0] }}
              </el-avatar>
              <span class="user-name">{{ authStore.userInfo?.nickName || '用户' }}</span>
              <el-icon><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="router.push('/orders')">我的订单</el-dropdown-item>
                <el-dropdown-item @click="router.push('/my-coupons')">我的优惠券</el-dropdown-item>
                <el-dropdown-item @click="router.push('/account')">个人中心</el-dropdown-item>
                <el-dropdown-item @click="router.push('/sign')">签到中心</el-dropdown-item>
                <el-dropdown-item divided @click="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <button type="button" class="icon-action" title="搜索" @click="openSearchPortal">
            <el-icon :size="22"><Search /></el-icon>
            <span class="icon-label">搜索</span>
          </button>

          <button type="button" class="icon-action" title="购物车" @click="router.push('/cart')">
            <el-badge :value="cartStore.cartCount" :hidden="!cartStore.cartCount" :max="99" class="cart-badge">
              <el-icon :size="22"><ShoppingCart /></el-icon>
            </el-badge>
            <span class="icon-label">购物车</span>
          </button>

          <button type="button" class="icon-action" title="智能客服" @click="router.push('/ai-assistant')">
            <el-icon :size="22"><ChatDotRound /></el-icon>
            <span class="icon-label">客服</span>
          </button>

          <button type="button" class="icon-action" title="消息" @click="router.push('/notifications')">
            <el-badge :value="unreadCount" :hidden="!unreadCount" :max="99">
              <el-icon :size="22"><Bell /></el-icon>
            </el-badge>
            <span class="icon-label">消息</span>
          </button>
        </nav>
      </div>
    </header>

    <el-drawer v-model="mobileMenuOpen" direction="rtl" size="72%" title="菜单">
      <div class="mobile-menu">
        <template v-if="!authStore.isLoggedIn">
          <el-button type="primary" class="menu-btn" @click="navAndClose('/login')">登录</el-button>
          <el-button @click="navAndClose('/register')">注册</el-button>
        </template>
        <el-menu :default-active="route.path" @select="navAndClose">
          <el-menu-item index="/">首页</el-menu-item>
          <el-menu-item index="/search">商品分类</el-menu-item>
          <el-menu-item index="/coupons">优惠券广场</el-menu-item>
          <el-menu-item index="/cart">购物车</el-menu-item>
          <el-menu-item index="/orders">我的订单</el-menu-item>
          <el-menu-item index="/account">个人中心</el-menu-item>
          <el-menu-item index="/notifications">消息中心</el-menu-item>
          <el-menu-item index="/member-center">会员中心</el-menu-item>
          <el-menu-item index="/ai-assistant">智能客服</el-menu-item>
        </el-menu>
      </div>
    </el-drawer>

    <main class="main-wrap">
      <div class="page-container">
        <PullRefreshHost>
          <PageBackBar v-if="showPageBack" />
          <RouterView v-slot="{ Component, route: viewRoute }">
            <KeepAlive :max="4">
              <component :is="Component" :key="viewRoute.path" class="page-view-root" />
            </KeepAlive>
          </RouterView>
        </PullRefreshHost>
      </div>
    </main>

    <AppFooter v-if="showFooter" />
    <MobileTabBar />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router';
import { ArrowDown, Bell, ChatDotRound, Menu, Search, Setting, ShoppingCart } from '@element-plus/icons-vue';
import { useAuthStore } from '@/stores/auth';
import { useCartStore } from '@/stores/cart';
import { productApi } from '@/api/modules';
import { useUnreadCount } from '@/composables/useUnreadCount';
import AppFooter from '@/components/layout/AppFooter.vue';
import MobileTabBar from '@/components/layout/MobileTabBar.vue';
import PageBackBar from '@/components/layout/PageBackBar.vue';
import TabPageHeader from '@/components/layout/TabPageHeader.vue';
import AgentServiceEntry from '@/components/agent/AgentServiceEntry.vue';
import BrandMark from '@/components/common/BrandMark.vue';
import HomeSearchHeader from '@/components/layout/HomeSearchHeader.vue';
import PullRefreshHost from '@/components/common/PullRefreshHost.vue';
import NotificationPopup from '@/components/business/NotificationPopup.vue';
import { isPrimaryTabPath } from '@/constants/tabPages';
import { resolveAvatarUrl } from '@/utils/image';
import { useSearchStore } from '@/stores/search';
import { flattenCategoryOptions, normalizeCategoryTree } from '@/utils/category';
import { confirmAction } from '@/utils/confirm';
import { restoreScrollForPath, saveScrollForPath } from '@/utils/scrollMemory';
import { navigateNotification, type NotificationData } from '@/utils/notification';
import { toast } from '@/utils/toast';

const router = useRouter();
const searchStore = useSearchStore();
const route = useRoute();
const authStore = useAuthStore();
const cartStore = useCartStore();

const keyword = ref('');
const searchCategoryId = ref('');
const categoryList = ref<any[]>([]);
const mobileMenuOpen = ref(false);
const isMobile = ref(false);
const { unreadCount, refreshUnreadCount } = useUnreadCount();

const isHomeTab = computed(() => route.path === '/');
const isPrimaryTab = computed(() => isPrimaryTabPath(route.path));

const showSiteHeader = computed(() => false);

const tabHeader = computed(() => {
  if (!isPrimaryTab.value || isHomeTab.value) return null;
  switch (route.path) {
    case '/search':
      if (isMobile.value) return null;
      return { title: '分类' };
    case '/cart':
      return {
        title:
          cartStore.cartCount > 0 ? `购物车(${cartStore.cartCount})` : '购物车'
      };
    case '/account':
      if (isMobile.value) return null;
      return { title: '我的' };
    default:
      return null;
  }
});

const layoutClasses = computed(() => ({
  'is-mobile': isMobile.value,
  'is-home-page': isHomeTab.value,
  'is-tab-page': !!tabHeader.value,
  'is-primary-tab': isMobile.value && isPrimaryTab.value,
  'is-account-tab': isMobile.value && route.path === '/account',
  'is-cate-tab': isMobile.value && route.path === '/search'
}));

const showFooter = computed(() => true);

const showPageBack = computed(() => {
  if (route.meta.level === 1 || isPrimaryTab.value) return false;
  return route.meta.showBack === true;
});

const avatarUrl = computed(() => resolveAvatarUrl(authStore.userInfo?.avatar));

const checkMobile = () => {
  isMobile.value = window.innerWidth < 768;
};

const openSearchPortal = () => {
  const keyWords = keyword.value.trim();
  if (keyWords) {
    searchStore.setSearch({
      keyWords,
      categoryId: searchCategoryId.value || ''
    });
  }
  router.push('/search-portal');
};

const goSearch = () => {
  const keyWords = keyword.value.trim();
  if (!keyWords) {
    openSearchPortal();
    return;
  }
  searchStore.setSearch({
    keyWords,
    categoryId: searchCategoryId.value || ''
  });
  router.push({ path: '/search-result', query: { q: keyWords } });
};

const navAndClose = (path: string) => {
  router.push(path);
  mobileMenuOpen.value = false;
};

const logout = async () => {
  const ok = await confirmAction('确定要退出当前账号吗？', {
    title: '退出登录',
    confirmButtonText: '退出'
  });
  if (!ok) return;
  authStore.prepareLogoutNavigation();
  await authStore.logout();
  toast.success('已退出登录');
  await router.replace({ path: '/login', query: {} });
};

const handleNotificationClick = (notification: NotificationData) => {
  void navigateNotification(router, notification, { refreshUnread: refreshUnreadCount });
};

watch(
  () => route.path,
  (path) => {
    if (path === '/cart' && authStore.isLoggedIn) {
      cartStore.fetchCartCount();
    }
    if (path === '/notifications' || path === '/account') {
      refreshUnreadCount();
    }
  },
  { immediate: true }
);

watch(
  () => authStore.isLoggedIn,
  (loggedIn) => {
    if (loggedIn) {
      refreshUnreadCount();
    } else {
      unreadCount.value = 0;
    }
  }
);

onMounted(async () => {
  checkMobile();
  window.addEventListener('resize', checkMobile);
  const cats = await productApi.loadCategory();
  categoryList.value = flattenCategoryOptions(normalizeCategoryTree(cats || []));
  if (authStore.isLoggedIn) {
    cartStore.fetchCartCount();
    refreshUnreadCount();
  }
});

onUnmounted(() => window.removeEventListener('resize', checkMobile));

router.beforeEach((to, from) => {
  if (!from.fullPath || to.fullPath === from.fullPath) return;
  const tabSwitch = isPrimaryTabPath(from.path) && isPrimaryTabPath(to.path);
  if (!tabSwitch) saveScrollForPath(from.fullPath);
});

const restoreScrollPosition = (path = route.fullPath) => {
  restoreScrollForPath(path);
};

onMounted(() => {
  router.afterEach((to, from) => {
    const tabSwitch =
      isPrimaryTabPath(to.path) && isPrimaryTabPath(from.path);
    if (tabSwitch) return;
    nextTick(() => restoreScrollPosition(to.fullPath));
  });
  restoreScrollPosition();
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.layout {
  min-height: 100vh;
  min-height: 100dvh;
  background: transparent;

  &.is-mobile {
    overflow-x: clip;
  }

  &.is-tab-page .main-wrap {
    padding-top: calc(#{$tab-header-height} + env(safe-area-inset-top, 0));
  }

  &.is-home-page .main-wrap {
    padding-top: calc(#{$home-search-bar-height} + env(safe-area-inset-top, 0));
  }

  &.is-account-tab .main-wrap {
    padding-top: 0;
  }

  &.is-cate-tab .main-wrap {
    padding-top: 0;
  }
}

.tab-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-action {
  color: $color-text-title;
  cursor: pointer;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: $color-bg-subtle;
  transition: background $transition-fast, color $transition-fast, transform $transition-fast;

  &:active {
    transform: scale(0.92);
    background: $color-primary-muted;
    color: $color-primary;
  }
}

.header-action-btn {
  border: none;
  padding: 0;
}

.site-header {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 1000;
  height: $header-height;
  background: #fff;
  border-bottom: 1px solid var(--glass-border-soft);
  box-shadow: var(--glass-shadow-sm);

  &::after {
    display: none;
  }

  .header-inner {
    max-width: $content-width;
    height: 100%;
    margin: 0 auto;
    padding: 0 16px;
    display: grid;
    grid-template-columns: auto minmax(200px, 1fr) auto;
    align-items: center;
    column-gap: 24px;
  }
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  justify-self: start;
  text-decoration: none;
  white-space: nowrap;
  transition: opacity $transition-fast;

  &:hover {
    opacity: 0.92;
  }

  .brand-icon {
    width: 36px;
    height: 40px;
  }

  .brand-text {
    font-size: 20px;
    font-weight: 600;
    color: $color-text-title;
    letter-spacing: 0.08em;
    white-space: nowrap;
    line-height: 1;
  }
}

.mobile-tools {
  display: none;
  justify-self: end;
  align-items: center;
  gap: 10px;

  .tool-btn {
    color: $color-text-title;
    cursor: pointer;
    width: 36px;
    height: 36px;
    padding: 0;
    border-radius: 50%;
    display: grid;
    place-items: center;
    background: $color-bg-subtle;
    transition: transform $transition-fast, background $transition-fast;

    &:active {
      transform: scale(0.92);
      background: rgba(255, 255, 255, 0.28);
    }
  }
}

.search-box {
  width: 100%;
  max-width: 560px;
  height: 42px;
  margin: 0 auto;
  justify-self: center;
  display: flex;
  align-items: center;
  background: #fff;
  border-radius: $radius-search;
  overflow: hidden;
  box-shadow: 0 4px 16px rgba(16, 24, 40, 0.1);
  transition: box-shadow $transition-fast, transform $transition-fast;

  &:focus-within {
    box-shadow: 0 6px 24px rgba(16, 24, 40, 0.14);
    transform: translateY(-1px);
  }

  .search-category {
    width: 110px;
    flex-shrink: 0;

    :deep(.el-select__wrapper) {
      box-shadow: none !important;
      background: transparent;
    }
  }

  .search-divider {
    width: 1px;
    height: 20px;
    background: $color-border;
    flex-shrink: 0;
  }

  .search-input {
    flex: 1;
    min-width: 0;
    border: none;
    outline: none;
    padding: 0 14px;
    font-size: 14px;
    color: $color-text-title;

    &::placeholder {
      color: $color-text-muted;
    }
  }

  .search-submit {
    flex-shrink: 0;
    height: 100%;
    padding: 0 20px;
    border: none;
    background: linear-gradient(90deg, $color-primary-hover, $color-primary);
    color: #fff;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 4px;
    transition: filter $transition-fast, transform $transition-fast;

    &:hover {
      filter: brightness(1.05);
      transform: translateY(-1px);
    }

    &:active {
      filter: brightness(0.95);
      transform: translateY(1px) scale(0.98);
    }

    &:focus-visible {
      outline: 2px solid $color-gold;
      outline-offset: 2px;
    }
  }
}

.header-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
  flex-shrink: 0;
  justify-self: end;

  .action-link {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: rgba(255, 255, 255, 0.95);
    font-size: 13px;
    line-height: 1.2;
    padding: 6px 10px;
    border-radius: $radius-btn;
    transition: background $transition-fast, color $transition-fast;

    &:hover {
      background: rgba(255, 255, 255, 0.15);
      color: #fff;
    }

    &.highlight {
      font-weight: 600;
    }
  }

  .user-trigger {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px 4px 4px;
    border: none;
    border-radius: 20px;
    background: rgba(255, 255, 255, 0.12);
    color: #fff;
    cursor: pointer;
    transition: background $transition-fast;

    &:hover {
      background: rgba(255, 255, 255, 0.22);
    }

    .user-name {
      max-width: 72px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 13px;
    }
  }

  .icon-action {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    min-width: 52px;
    padding: 4px 8px;
    border: none;
    background: transparent;
    color: #fff;
    cursor: pointer;
    border-radius: $radius-btn;
    transition: background $transition-fast, transform $transition-fast;
    text-align: center;

    .el-icon {
      display: block;
    }

    .icon-label {
      display: block;
      width: 100%;
      font-size: 11px;
      line-height: 1.2;
      text-align: center;
      opacity: 0.95;
    }

    &:hover {
      background: rgba(255, 255, 255, 0.15);
    }

    &:active {
      transform: scale(0.95);
    }
  }

  :deep(.cart-badge .el-badge__content) {
    background: $color-price;
    border: 2px solid $color-primary;
    animation: badge-pop 0.35s ease;
  }
}

@keyframes badge-pop {
  0% {
    transform: scale(0.6);
  }
  70% {
    transform: scale(1.15);
  }
  100% {
    transform: scale(1);
  }
}

.main-wrap {
  padding-top: 16px;
  min-height: calc(100vh - #{$footer-height});
}

.page-container {
  max-width: $content-width;
  margin: 0 auto;
  padding: 0 16px 16px;

  .layout.is-mobile.is-home-page & {
    max-width: 100%;
    padding: 0;
  }

  .layout.is-mobile.is-primary-tab & {
    padding: 0 0 $mobile-tab-reserved;
  }

  .layout.is-mobile .page-container:has(.agent-page) {
    padding-bottom: 0;
  }

  @media (min-width: $breakpoint-tablet) {
    padding: 0 16px 24px;

    .layout.is-home-page & {
      max-width: $content-width;
    }
  }
}

.mobile-search-panel {
  padding: 16px;
}

.mobile-menu .menu-btn {
  width: 100%;
  margin-bottom: 8px;
}

@media (max-width: $breakpoint-tablet) {
  .header-inner {
    column-gap: 12px;
    grid-template-columns: auto 1fr auto;
  }

  .search-box {
    max-width: 100%;
  }

  .header-actions .icon-label {
    display: none;
  }

  .header-actions .icon-action {
    min-width: 40px;
    padding: 6px;
  }
}

@media (max-width: $breakpoint-mobile) {
  .brand {
    max-width: calc(100vw - 130px);
    overflow: hidden;
  }

  .brand .brand-text {
    font-size: 16px;
    white-space: nowrap;
  }

  .header-inner {
    grid-template-columns: auto 1fr auto;
    column-gap: 8px;
  }

  .search-box,
  .header-actions {
    display: none;
  }

  .mobile-tools {
    display: flex;
  }
}
</style>
