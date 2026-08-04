<template>
  <div class="page-nav-bar ignore">
    <button type="button" class="nav-back" aria-label="返回" @click="goBack">
      <el-icon :size="20"><ArrowLeft /></el-icon>
    </button>
    <h1 class="nav-title">{{ displayTitle }}</h1>
    <div class="nav-side-placeholder" aria-hidden="true" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ArrowLeft } from '@element-plus/icons-vue';
import { recoverIosViewportZoom } from '@/utils/mobileViewport';

const props = defineProps<{
  title?: string;
  fallback?: string;
}>();

const router = useRouter();
const route = useRoute();

const displayTitle = computed(() => props.title || (route.meta.title as string) || '');

const fallbackPath = computed(() => props.fallback || '/');

const isAuthPage = () => route.path === '/login' || route.path === '/register';

const canHistoryBackSafely = () => {
  const back = window.history.state?.back as string | null | undefined;
  if (!back) return false;

  let backResolved;
  try {
    backResolved = router.resolve(back);
  } catch {
    return false;
  }

  if (backResolved.fullPath === route.fullPath) return false;

  const redirectRaw = route.query.redirect;
  if (typeof redirectRaw === 'string' && redirectRaw) {
    try {
      if (router.resolve(redirectRaw).fullPath === backResolved.fullPath) return false;
    } catch {

    }
  }

  return !backResolved.matched.some((r) => r.meta.requiresAuth === true);
};

const goBack = () => {
  recoverIosViewportZoom();
  (document.activeElement as HTMLElement | null)?.blur?.();

  if (isAuthPage() && route.query.redirect) {
    if (canHistoryBackSafely()) {
      router.back();
    } else {
      router.replace(fallbackPath.value);
    }
    return;
  }

  if (window.history.length > 1) {
    router.back();
    return;
  }
  router.replace(fallbackPath.value);
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.page-nav-bar {
  display: grid;
  grid-template-columns: 44px 1fr 44px;
  align-items: center;
  height: 48px;
  margin-bottom: 12px;
  padding: 0;
}

.nav-back {
  width: 40px;
  height: 40px;
  margin: 0 auto;
  padding: 0;
  border: 1px solid rgba(120, 120, 128, 0.18);
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.55);
  -webkit-backdrop-filter: var(--glass-blur-sm);
  backdrop-filter: var(--glass-blur-sm);
  color: var(--m-ink, #333333);
  cursor: pointer;
  display: grid;
  place-items: center;
  touch-action: manipulation;
  -webkit-tap-highlight-color: transparent;
  transition: background 0.2s, color 0.2s, transform 0.15s, box-shadow 0.2s;

  &:hover {
    background: rgba(255, 255, 255, 0.78);
    color: var(--m-gold, #2563eb);
  }

  &:active {
    transform: scale(0.94);
    box-shadow: none;
  }
}

.nav-title {
  margin: 0;
  padding: 0 8px;
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 0;
  line-height: 1.3;
  color: $color-text-title;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-side-placeholder {
  width: 40px;
  height: 40px;
}
</style>
