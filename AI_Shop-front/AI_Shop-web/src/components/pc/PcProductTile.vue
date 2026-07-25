<template>
  <article class="pc-product-tile" @click="$emit('click', product)">
    <div class="pc-product-tile__cover">
      <ProductImage :product="product" fit="contain" width="100%" height="100%" />
    </div>
    <p class="pc-product-tile__name">{{ product.productName || product.name }}</p>
    <p class="pc-product-tile__price">
      <span class="sym">¥</span>{{ displayPrice }}
    </p>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import ProductImage from '@/components/common/ProductImage.vue';

const props = defineProps<{ product: Record<string, any> }>();
defineEmits<{ click: [Record<string, any>] }>();

const displayPrice = computed(() => {
  const p = props.product.price ?? props.product.salePrice ?? props.product.minPrice;
  return p != null ? Number(p).toFixed(2) : '--';
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-product-tile {
  cursor: pointer;
  border: 1px solid $color-border-light;
  border-radius: $radius-sm;
  padding: 10px 10px 12px;
  background: #fff;
  min-width: 0;
  transition: border-color $transition-fast, box-shadow 0.45s cubic-bezier(0.25, 0.1, 0.25, 1), transform 0.45s cubic-bezier(0.25, 0.1, 0.25, 1);

  &:hover {
    border-color: rgba($color-primary, 0.25);
    box-shadow: $shadow-card-hover;
    transform: translateY(-3px);

    .pc-product-tile__cover :deep(img) {
      transform: scale(1.04);
    }
  }

  &__cover {
    aspect-ratio: 1;
    margin-bottom: 8px;
    border-radius: $radius-xs;
    overflow: hidden;
    background: #fafafa;

    :deep(.product-image),
    :deep(.el-image),
    :deep(.el-image__inner) {
      width: 100% !important;
      height: 100% !important;
    }

    :deep(img) {
      transition: transform $transition-normal;
    }
  }

  &__name {
    margin: 0 0 6px;
    font-size: 14px;
    line-height: 1.45;
    color: $color-text-primary;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: normal;
    overflow-wrap: break-word;
  }

  &__price {
    margin: 0;
    font-size: 18px;
    font-weight: 700;
    line-height: 1.2;
    color: $color-primary;
    white-space: nowrap;

    .sym {
      font-size: 13px;
      font-weight: 600;
    }
  }
}
</style>
