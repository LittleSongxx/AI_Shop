<template>
  <div
    class="sub-page-layout"
    :class="{
      'is-mobile': isMobile,
      'no-tab-bar': hideTabBar,
      'is-agent-page': isAgentPage
    }"
  >
    <LiquidGlassSurface
      ref="subTopRef"
      tag="header"
      intensity="medium"
      class="sub-top-bar ignore"
    >
      <div class="sub-top-inner">
        <PageBackBar fallback="/" />
      </div>
    </LiquidGlassSurface>
    <main class="sub-main">
      <div class="sub-container" :class="{ 'is-agent-container': isAgentPage }">
        <RouterView v-slot="{ Component }">
          <Transition name="page-fade" mode="out-in">
            <component :is="Component" />
          </Transition>
        </RouterView>
      </div>
    </main>
    <MobileTabBar v-if="!hideTabBar" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';
import { RouterView, useRoute, useRouter } from 'vue-router';
import LiquidGlassSurface from '@/components/common/LiquidGlassSurface.vue';
import PageBackBar from '@/components/layout/PageBackBar.vue';
import MobileTabBar from '@/components/layout/MobileTabBar.vue';
import { useCartStore } from '@/stores/cart';
import { useAuthStore } from '@/stores/auth';
import { restoreScrollForPath, saveScrollForPath } from '@/utils/scrollMemory';

const route = useRoute();
const router = useRouter();
const cartStore = useCartStore();
const authStore = useAuthStore();
const isMobile = ref(false);
const subTopRef = ref<InstanceType<typeof LiquidGlassSurface> | null>(null);

router.beforeEach((to, from) => {
  if (from.fullPath && to.fullPath !== from.fullPath) {
    saveScrollForPath(from.fullPath);
  }
});

const restoreScrollPosition = () => {
  restoreScrollForPath(route.fullPath);
};

onMounted(() => {
  checkMobile();
  window.addEventListener('resize', checkMobile);
  window.addEventListener('resize', syncSubTopHeight);
  syncSubTopHeight();
  if (authStore.isLoggedIn) cartStore.fetchCartCount();
  router.afterEach(() => nextTick(() => restoreScrollPosition()));
  restoreScrollPosition();
});

const syncSubTopHeight = () => {
  const el = subTopRef.value?.$el as HTMLElement | undefined;
  const h = el?.offsetHeight;
  if (h && h > 0) {
    document.documentElement.style.setProperty('--sub-top-height', `${h}px`);
  }
};

const hideTabBar = computed(() => route.matched.some((r) => r.meta.hideTabBar === true));

const isAgentPage = computed(() => route.path === '/ai-assistant' || route.path.endsWith('/ai-assistant'));

const checkMobile = () => {
  isMobile.value = window.innerWidth < 768;
};

onUnmounted(() => {
  window.removeEventListener('resize', checkMobile);
  window.removeEventListener('resize', syncSubTopHeight);
  document.documentElement.style.removeProperty('--sub-top-height');
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.sub-page-layout {
  min-height: 100vh;
  background: var(--glass-page-bg);
  overflow-x: clip;

  &.is-mobile.no-tab-bar .sub-main {
    padding-bottom: calc(env(safe-area-inset-bottom, 0) + 12px);
  }

  &.is-mobile .sub-main:has(.agent-page),
  &.is-mobile .sub-main:has(.checkout-page),
  &.is-mobile .sub-main:has(.pay-page) {
    padding-bottom: 0;
  }

  &.is-mobile.no-tab-bar.is-agent-page {
    display: flex;
    flex-direction: column;
    min-height: var(--app-vh, 100dvh);

    .sub-main {
      flex: 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
  }
}

.sub-page-layout.is-agent-page {
  --sub-top-height: 60px;
  display: flex;
  flex-direction: column;
  height: var(--app-vh, 100dvh);
  max-height: var(--app-vh, 100dvh);
  min-height: var(--app-vh, 100dvh);
  overflow: hidden;

  .sub-top-bar {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 1300;
    padding-top: env(safe-area-inset-top, 0);
    background: var(--glass-bg-header);
  }

  .sub-main {
    flex: 1 1 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    padding: 0;
    padding-top: calc(var(--sub-top-height, 60px) + env(safe-area-inset-top, 0));
  }

  .sub-container {
    flex: 1 1 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    width: 100%;
    max-width: none;
    margin: 0;
    padding: 0;
  }
}

.sub-page-layout:has(.agent-page) {
  --sub-top-height: 60px;
  display: flex;
  flex-direction: column;
  height: var(--app-vh, 100dvh);
  max-height: var(--app-vh, 100dvh);
  min-height: var(--app-vh, 100dvh);
  overflow: hidden;

  .sub-top-bar {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 1300;
    padding-top: env(safe-area-inset-top, 0);
    background: #fff;
  }

  .sub-main {
    flex: 1 1 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    padding: 0;
    padding-top: calc(var(--sub-top-height, 60px) + env(safe-area-inset-top, 0));
  }

  .sub-container {
    flex: 1 1 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
}

.sub-container.is-agent-container,
.sub-container:has(.agent-page) {
  max-width: none;
  width: 100%;
  margin: 0;
  padding: 0;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;

  :deep(.pull-refresh-host) {
    flex: 1 1 auto;
    min-height: 0;
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;

    > * {
      flex: 1 1 auto;
      min-height: 0;
      height: 100%;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
  }

  :deep(.agent-page) {
    flex: 1 1 0;
    min-height: 0;
    height: 100%;
    display: flex;
    flex-direction: column;
    width: 100%;
    overflow: hidden;
  }

  :deep(.chat-scroll) {
    flex: 1 1 0;
    min-height: 0;
    overflow-x: clip;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
    touch-action: pan-y;
  }
}

.sub-top-bar {
  position: sticky;
  top: 0;
  z-index: 100;
  background: #fff;
  border-bottom: 1px solid var(--glass-border-soft);
  box-shadow: var(--glass-shadow-sm);
}

.sub-page-layout.is-mobile .sub-top-bar {
  padding-top: env(safe-area-inset-top, 0);
}

.sub-top-inner {
  max-width: $content-width;
  margin: 0 auto;
  padding: 8px 16px;

  :deep(.page-nav-bar) {
    margin-bottom: 0;
    box-shadow: none;
    border: none;
    background: transparent;
    height: 44px;
    padding: 0;
  }
}

.sub-main {
  padding: 16px 0 24px;

  &:has(.agent-page) {
    padding: 0;
  }
}

.sub-page-layout.is-mobile .sub-main:has(.orders-page) {
  padding-top: 0;
}

.sub-page-layout.is-mobile .sub-container:has(.orders-page) {
  padding-top: 0;
  padding-left: 0;
  padding-right: 0;
}

.sub-page-layout.is-mobile:has(.orders-page) .sub-top-bar {
  border-bottom: none;
  box-shadow: none;
}

.sub-container {
  max-width: $content-width;
  margin: 0 auto;
  padding: 0 16px;

  &:has(.auth-page) {
    max-width: none;
    padding: 0;
  }
}

.sub-main:has(.auth-page) {
  padding: 0;
}
</style>
