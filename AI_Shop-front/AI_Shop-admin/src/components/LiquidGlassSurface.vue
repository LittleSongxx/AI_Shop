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

<script setup>
import { computed, onMounted, ref, useAttrs, watch } from 'vue'
import { applyLiquidGlassEffect } from '@/utils/liquidGlassFilters.js'

const props = defineProps({
  tag: { type: String, default: 'div' },
  intensity: { type: String, default: 'medium' },
  variant: { type: String, default: 'default' }
})

defineOptions({ inheritAttrs: false })

const attrs = useAttrs()
const rootRef = ref(null)

const passthroughAttrs = computed(() => {
  const { class: _class, ...rest } = attrs
  return rest
})

function resolveRootEl() {
  const raw = rootRef.value
  if (!raw) return null
  if (raw instanceof HTMLElement) return raw
  return raw.$el ?? null
}

function syncEffectStyles() {
  const effect = resolveRootEl()?.querySelector('.liquid-glass-surface__effect')
  if (!effect) return
  applyLiquidGlassEffect(effect, props.intensity, props.variant === 'active')
}

onMounted(syncEffectStyles)
watch(() => [props.intensity, props.variant], syncEffectStyles)
</script>
