<template>
  <Teleport to="body">
  <div ref="hostRef" class="mobile-tab-bar-host" :class="{ 'is-jelly': barJelly }">
  <LiquidGlassSurface
    tag="nav"
    intensity="strong"
    class="mobile-tab-bar ignore"
    aria-label="底部导航"
  >
    <div
      class="tab-bar-inner"
      :style="{ '--tab-count': tabs.length, '--active-index': activeIndex }"
    >
      <LiquidGlassSurface
        v-show="activeIndex >= 0"
        intensity="strong"
        variant="active"
        class="tab-active-glass"
        :class="{ 'is-jelly': glassJelly }"
        aria-hidden="true"
      />

      <RouterLink
        v-for="item in tabs"
        :key="item.label"
        :to="item.path"
        replace
        class="tab-item"
        :class="{ active: isActive(item) }"
        :aria-current="isActive(item) ? 'page' : undefined"
        :aria-label="item.label"
        @click="onTabPress()"
      >
        <span class="icon-wrap">
          <el-badge v-if="item.badge" :value="cartCount" :hidden="!cartCount" :max="99">
            <el-icon :size="16"><component :is="item.icon" /></el-icon>
          </el-badge>
          <el-icon v-else :size="16"><component :is="item.icon" /></el-icon>
        </span>
        <span class="label">{{ item.label }}</span>
      </RouterLink>
    </div>
  </LiquidGlassSurface>
  </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import { HomeFilled, Grid, ShoppingCart, User } from '@element-plus/icons-vue';
import LiquidGlassSurface from '@/components/common/LiquidGlassSurface.vue';
import { useTabBarJelly } from '@/composables/useTabBarJelly';
import { useCartStore } from '@/stores/cart';
import { useAuthStore } from '@/stores/auth';

const route = useRoute();
const cartStore = useCartStore();
const authStore = useAuthStore();
const hostRef = ref<HTMLElement | null>(null);
const cartCount = computed(() => cartStore.cartCount);

const syncTabStackHeight = () => {
  const el = hostRef.value;
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const stackHeight = window.innerHeight - rect.top;
  document.documentElement.style.setProperty('--mobile-tab-stack-height', `${stackHeight}px`);
};

onMounted(() => {
  nextTick(syncTabStackHeight);
  window.addEventListener('resize', syncTabStackHeight);
  window.visualViewport?.addEventListener('resize', syncTabStackHeight);
  window.visualViewport?.addEventListener('scroll', syncTabStackHeight);
});

onUnmounted(() => {
  window.removeEventListener('resize', syncTabStackHeight);
  window.visualViewport?.removeEventListener('resize', syncTabStackHeight);
  window.visualViewport?.removeEventListener('scroll', syncTabStackHeight);
});

const tabs = computed(() => [
  { path: '/', label: '首页', icon: HomeFilled, match: (p: string) => p === '/' },
  {
    path: '/search',
    label: '分类',
    icon: Grid,
    match: (p: string) => p === '/search' || p.startsWith('/category/')
  },
  { path: '/cart', label: '购物车', icon: ShoppingCart, badge: true, match: (p: string) => p === '/cart' },
  {
    path: authStore.isLoggedIn ? '/account' : '/login',
    label: '我的',
    icon: User,
    match: (p: string) => p === '/account' || (!authStore.isLoggedIn && p === '/login')
  }
]);

const isActive = (item: (typeof tabs.value)[0]) => item.match(route.path);

const activeIndex = computed(() => {
  const idx = tabs.value.findIndex((item) => isActive(item));
  return idx >= 0 ? idx : -1;
});

const { barJelly, glassJelly, onTabPress } = useTabBarJelly();
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;
@use '@/styles/tab-bar-jelly' as jelly;

.mobile-tab-bar-host {
  display: none;
  position: fixed;
  left: 2.5%;
  right: 2.5%;
  bottom: calc(12px + env(safe-area-inset-bottom, 0));
  z-index: 99999;
  width: auto;
  pointer-events: none;
  touch-action: manipulation;
  @include jelly.tab-bar-jelly-host;

  @media (max-width: $breakpoint-mobile) {
    display: block;
  }
}

.mobile-tab-bar-host .mobile-tab-bar {
  pointer-events: auto;
}

.mobile-tab-bar {
  position: relative !important;
  left: auto !important;
  right: auto !important;
  bottom: auto !important;
  transform: none !important;
  z-index: auto !important;
  width: 100% !important;
  padding: 0 !important;
  border: none !important;
  border-radius: 999px !important;
  color: inherit;
  cursor: default;
  @include jelly.tab-bar-jelly-shell;
  box-shadow:
    0 6px 6px rgba(0, 0, 0, 0.2),
    0 0 20px rgba(0, 0, 0, 0.1);

}

.tab-bar-inner {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-around;
  width: 100%;
  height: 40px;
  padding: 4px 6px;
  overflow: visible;
}

.tab-active-glass {
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 6px;
  width: calc((100% - 12px) / var(--tab-count));
  border-radius: 999px;
  pointer-events: none;
  z-index: 0;
  transform: translateX(calc(var(--active-index) * 100%));
  box-shadow:
    0 2px 6px rgba(0, 0, 0, 0.12),
    0 0 12px rgba(0, 0, 0, 0.06);
  @include jelly.tab-bar-jelly-glass;
}

.tab-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  color: var(--m-ink-3, #8a8a8f);
  text-decoration: none;
  -webkit-tap-highlight-color: transparent;
  transition: color 0.25s ease;
  padding: 0 4px;
  cursor: pointer;
  @include jelly.tab-bar-jelly-items;

  .icon-wrap {
    display: grid;
    place-items: center;
    width: 18px;
    height: 18px;
  }

  .label {
    font-size: 9px;
    line-height: 1;
    font-weight: 500;
    transition: font-weight 0.2s;
  }

  &.active {
    color: var(--m-gold, #2563eb);

    .label {
      font-weight: 600;
    }

    .icon-wrap {
      transform: scale(1.08);
    }

    .el-icon {
      color: var(--m-gold, #2563eb);
    }
  }
}

:deep(.el-badge__content) {
  border: 2px solid rgba(255, 255, 255, 0.72);
  background: $color-primary;
  font-weight: 600;
  font-size: 10px;
}
</style>
