<template>
  <component
    :is="tag"
    ref="rootRef"
    class="liquid-glass-surface"
    :class="[
      `liquid-glass-surface--${intensity}`,
      variant !== 'default' ? `liquid-glass-surface--${variant}` : null,
      attrs.class
    ]"
    v-bind="passthroughAttrs"
  >
    <div class="liquid-glass-surface__effect" aria-hidden="true" />
    <div class="liquid-glass-surface__tint" aria-hidden="true" />
    <div class="liquid-glass-surface__shine" aria-hidden="true" />
    <div class="liquid-glass-surface__content">
      <slot />
    </div>
  </component>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, useAttrs, watch } from 'vue';
import {
  applyLiquidGlassEffect,
  type LiquidGlassIntensity
} from '@/utils/liquidGlassFilters';

export type { LiquidGlassIntensity };
export type LiquidGlassVariant = 'default' | 'active';

const props = withDefaults(
  defineProps<{
    tag?: string;
    intensity?: LiquidGlassIntensity;
    variant?: LiquidGlassVariant;
  }>(),
  {
    tag: 'div',
    intensity: 'medium',
    variant: 'default'
  }
);

defineOptions({ inheritAttrs: false });

const attrs = useAttrs();
const rootRef = ref<HTMLElement | null>(null);

const passthroughAttrs = computed(() => {
  const { class: _class, ...rest } = attrs;
  return rest;
});

function resolveRootEl(): HTMLElement | null {
  const raw = rootRef.value as HTMLElement | { $el?: HTMLElement } | null;
  if (!raw) return null;
  if (raw instanceof HTMLElement) return raw;
  return raw.$el ?? null;
}

function syncEffectStyles() {
  const effect = resolveRootEl()?.querySelector<HTMLElement>('.liquid-glass-surface__effect');
  if (!effect) return;
  applyLiquidGlassEffect(effect, props.intensity, props.variant === 'active');
}

onMounted(syncEffectStyles);
watch(() => [props.intensity, props.variant], syncEffectStyles);
</script>
