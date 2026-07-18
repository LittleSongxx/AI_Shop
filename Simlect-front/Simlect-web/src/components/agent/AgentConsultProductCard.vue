<template>
  <component
    :is="clickable ? 'button' : 'div'"
    type="button"
    class="consult-product-card"
    :class="{ 'is-composer': variant === 'composer', 'is-bubble': variant === 'bubble' }"
    @click="onClick"
  >
    <div class="cover-wrap">
      <ProductImage :source="product.cover" class="cover" width="100%" height="100%" fit="cover" />
    </div>
    <div class="meta">
      <p class="name">{{ product.productName }}</p>
      <p v-if="priceText" class="price">¥{{ priceText }}</p>
      <p v-if="variant === 'composer'" class="hint">
        {{ resuming ? '点击继续上次咨询' : '点击卡片，开始咨询这件商品' }}
      </p>
    </div>
  </component>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import ProductImage from '@/components/common/ProductImage.vue';
import type { AgentConsultProduct } from '@/utils/agentProductConsult';

const props = withDefaults(
  defineProps<{
    product: AgentConsultProduct;
    variant?: 'composer' | 'bubble';
    clickable?: boolean;

    resuming?: boolean;
  }>(),
  { variant: 'composer', clickable: false, resuming: false }
);

const emit = defineEmits<{ send: [] }>();

const priceText = computed(() => {
  const n = Number(props.product.minPrice);
  return Number.isFinite(n) ? n.toFixed(2) : '';
});

const onClick = () => {
  if (props.clickable) emit('send');
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.consult-product-card {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 0;
  border: none;
  border-radius: $radius-card;
  background: transparent;
  text-align: left;
  -webkit-tap-highlight-color: transparent;

  &.is-composer {
    padding: 10px 12px;
    border-radius: 12px;
    background: rgba(255, 255, 255, 0.75);
    background: var(--glass-bg-light, rgba(255, 255, 255, 0.75));
    -webkit-backdrop-filter: blur(12px) saturate(160%);
    -webkit-backdrop-filter: var(--glass-blur-sm, blur(12px) saturate(160%));
    backdrop-filter: blur(12px) saturate(160%);
    backdrop-filter: var(--glass-blur-sm, blur(12px) saturate(160%));
    border: 1px solid rgba(120, 120, 128, 0.18);
    border: 1px solid var(--glass-border-soft, rgba(120, 120, 128, 0.18));
    box-shadow: 0 2px 12px rgba(17, 23, 41, 0.08);
    box-shadow: var(--glass-shadow-sm, 0 2px 12px rgba(17, 23, 41, 0.08));
  }

  &.is-bubble {
    width: fit-content;
    max-width: min(75%, 280px);
    margin-left: auto;
    padding: 8px 10px;
    background: var(--glass-bg-light);
    -webkit-backdrop-filter: var(--glass-blur-sm);
    backdrop-filter: var(--glass-blur-sm);
    border: 1px solid var(--glass-border);
    box-shadow: var(--glass-shadow-sm);
  }

  &:is(button) {
    cursor: pointer;

    &:active {
      opacity: 0.85;
    }
  }
}

.cover-wrap {
  flex: 0 0 48px;
  width: 48px;
  height: 48px;
  border-radius: $radius-sm;
  overflow: hidden;
  background: $color-bg-subtle;

  :deep(.product-image) {
    width: 100% !important;
    height: 100% !important;
  }
}

.meta {
  flex: 1;
  min-width: 0;

  .name {
    margin: 0 0 4px;
    font-size: 13px;
    font-weight: 600;
    color: $color-text-title;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .price {
    margin: 0;
    font-size: 14px;
    font-weight: 700;
    color: $color-price;
  }

  .hint {
    margin: 4px 0 0;
    font-size: 11px;
    color: $color-primary;
  }
}
</style>
