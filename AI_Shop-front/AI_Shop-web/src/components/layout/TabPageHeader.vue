<template>
  <LiquidGlassSurface tag="header" intensity="medium" class="tab-page-header ignore">
    <div class="side side-left">
      <slot name="left" />
    </div>
    <div class="center">
      <h1 class="title">{{ title }}</h1>
      <span v-if="subtitle" class="subtitle">{{ subtitle }}</span>
    </div>
    <div class="side side-right">
      <slot name="right" />
    </div>
  </LiquidGlassSurface>
</template>

<script setup lang="ts">
import LiquidGlassSurface from '@/components/common/LiquidGlassSurface.vue';

defineProps<{
  title: string;
  subtitle?: string;
}>();
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.tab-page-header {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 1000;
  height: $tab-header-height;
  background: #fff;
  padding: 0 $app-page-gutter;
  padding-top: env(safe-area-inset-top, 0);
  min-height: calc($tab-header-height + env(safe-area-inset-top, 0));
  border-bottom: 1px solid var(--glass-border-soft);
  box-shadow: var(--glass-shadow-sm);
}

.tab-page-header :deep(.liquid-glass-surface__content) {
  display: grid;
  grid-template-columns: 44px 1fr 44px;
  align-items: center;
  width: 100%;
  height: 100%;
}

.side {
  display: flex;
  align-items: center;
  min-height: 44px;
}

.side-right {
  justify-content: flex-end;
}

.center {
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 6px;
  min-width: 0;
  padding: 0 4px;
}

.title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.03em;
  color: $color-text-title;
  white-space: nowrap;
}

.subtitle {
  font-size: 12px;
  font-weight: 500;
  color: $color-text-muted;
  white-space: nowrap;
}
</style>
