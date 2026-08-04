<template>
  <article
    class="product-card"
    :class="{ 'is-compact': compact, ignore: isDesktop }"
    @click="$emit('click', product)"
  >
    <div class="cover-wrap">
      <ProductImage
        :product="product"
        class="cover"
        width="100%"
        height="100%"
        :fit="resolvedFit"
        :lazy="imageLazy"
      />

      <button
        type="button"
        class="cart-float"
        aria-label="选择规格"
        @click.stop="goSkuPick"
      >
        <el-icon :size="18"><ShoppingCart /></el-icon>
      </button>
      <div class="hover-mask">
        <el-button type="primary" round size="small" @click.stop="goSkuPick">
          选规格
        </el-button>
      </div>
    </div>
    <div class="info">
      <h4 class="name">
        {{ product.productName || product.name }}
      </h4>
      <div class="price-row">
        <span class="price"><span class="symbol">¥</span>{{ displayPrice }}</span>
      </div>
      <p class="sale">销量 {{ product.totalSale ?? product.saleCount ?? 0 }}</p>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { ShoppingCart } from '@element-plus/icons-vue';
import ProductImage from '@/components/common/ProductImage.vue';
import { useDevice } from '@/composables/useDevice';
import { useProductSkuSheet } from '@/composables/useProductSkuSheet';

const { isDesktop } = useDevice();

const props = withDefaults(
  defineProps<{
    product: Record<string, any>;
    compact?: boolean;

    imageFit?: 'cover' | 'contain' | 'fill';
    imageLazy?: boolean;
  }>(),
  { compact: false, imageLazy: true }
);

const { open: openSkuSheet } = useProductSkuSheet();
const resolvedFit = computed(() => props.imageFit ?? (props.compact ? 'contain' : 'cover'));
defineEmits<{ click: [Record<string, any>]; addToCart: [Record<string, any>] }>();

const goSkuPick = () => {
  const id = props.product?.productId;
  if (!id) return;
  openSkuSheet(id);
};

const displayPrice = computed(() => {
  const p = props.product.price ?? props.product.salePrice ?? props.product.minPrice;
  return p != null ? Number(p).toFixed(2) : '--';
});

</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.product-card:not(.ignore) {
  position: relative;
  background: $color-card;
  border-radius: $radius-card;
  overflow: hidden;
  cursor: pointer;
  border: 1px solid $color-border-light;
  box-shadow: $shadow-card;
  transition: transform 0.3s ease,
              box-shadow 0.3s ease,
              border-color 0.3s ease,
              opacity 0.3s ease;

  @media (hover: hover) {
    &:hover {
      transform: translateY(-4px);
      box-shadow: 0 12px 24px rgba(0, 0, 0, 0.1);
      border-color: rgba($color-gold, 0.3);

      .hover-mask {
        opacity: 1;
      }

      .cart-float {
        opacity: 1;
        transform: scale(1);
        box-shadow: 0 4px 12px rgba($color-gold, 0.35);
      }
    }
  }

  &:active {
    transform: scale(0.97);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
    transition-duration: 0.1s;
  }

  &:focus-visible {
    outline: 2px solid $color-gold;
    outline-offset: 3px;
  }

  .cover-wrap {
    position: relative;
    width: 100%;
    aspect-ratio: 1;
    background: $color-bg-subtle;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;

    :deep(.product-image) {
      width: 100%;
      height: 100%;
    }

  }

  .cart-float {
    position: absolute;
    bottom: 8px;
    right: 8px;
    z-index: 2;
    width: 32px;
    height: 32px;
    border: none;
    border-radius: 50%;
    background: $color-primary;
    color: #fff;
    display: grid;
    place-items: center;
    cursor: pointer;
    opacity: 1;
    transform: scale(1);
    box-shadow: $shadow-xs;
    transition: transform $transition-fast, background $transition-fast;

    &:hover {
      background: $color-primary-hover;
      color: #fff;
    }
  }

  .hover-mask {
    position: absolute;
    inset: 0;
    background: linear-gradient(180deg, transparent 30%, rgba(0, 0, 0, 0.45));
    display: flex;
    align-items: flex-end;
    justify-content: center;
    padding-bottom: 16px;
    opacity: 0;
    transition: opacity $transition-normal;
    pointer-events: none;

    .el-button {
      pointer-events: auto;
    }
  }

  .info {
    padding: 10px 12px 12px;
  }

  .name {
    margin: 0 0 6px;
    font-size: 13px;
    font-weight: 500;
    line-height: 1.45;
    color: $color-text-title;
    height: 38px;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }

  .price-row {
    margin-bottom: 4px;
    display: flex;
    align-items: baseline;
    gap: 4px;

    .price {
      color: $color-price;
      font-size: 18px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      letter-spacing: 0;
      line-height: 1.2;

      .symbol {
        font-size: 12px;
        margin-right: 1px;
        font-weight: 600;
      }
    }
  }

  .sale {
    margin: 0;
    font-size: 11px;
    color: $color-text-muted;
  }

  &.is-compact {
    border-radius: $radius-card;
    border: none;
    box-shadow: none;

    @media (hover: hover) {
      &:hover {
        transform: none;
        box-shadow: $shadow-xs;
      }
    }

    .cover-wrap {
      aspect-ratio: 1;
      padding: 0;
      background: $color-card;
      border-radius: $radius-card $radius-card 0 0;
      overflow: hidden;

      :deep(.product-image) {
        border-radius: 0;
      }
    }

    .cart-float {
      width: 28px;
      height: 28px;
      bottom: 4px;
      right: 4px;
      opacity: 1;
      transform: scale(1);
    }

    .hover-mask {
      display: none;
    }

    .info {
      padding: 8px 10px 10px;
    }

    .name {
      font-size: 12px;
      height: 32px;
      line-height: 1.35;
      font-weight: 500;
    }

    .price-row {
      margin-bottom: 2px;

      .price {
        font-size: 16px;

        .symbol {
          font-size: 11px;
        }
      }
    }

    .sale {
      font-size: 10px;
      color: $color-text-disabled;
    }
  }
}

.product-card.ignore {
  position: relative;
  display: flex;
  flex-direction: column;
  height: auto;
  min-height: 0;
  background: $color-card;
  border-radius: $radius-sm;
  overflow: hidden;
  cursor: pointer;
  border: 1px solid $color-border-light;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  transition:
    border-color 0.3s cubic-bezier(0.4, 0, 0.2, 1),
    box-shadow 0.4s cubic-bezier(0.34, 1.56, 0.64, 1),
    transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);

  .cover-wrap {
    flex-shrink: 0;
    width: 100%;
    aspect-ratio: 1;
    background: linear-gradient(180deg, #fafafa 0%, #f5f5f5 100%);
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    border-radius: 7px 7px 0 0;
    transition: background 0.3s ease;

    :deep(.product-image),
    :deep(.el-image),
    :deep(.el-image__inner) {
      width: 100% !important;
      height: 100% !important;
      border-radius: 0 !important;
    }

    :deep(img) {
      transition: transform 0.5s cubic-bezier(0.4, 0, 0.2, 1);
    }
  }

  .cart-float {
    position: absolute;
    bottom: 8px;
    right: 8px;
    z-index: 2;
    width: 32px;
    height: 32px;
    border: none;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.98);
    color: $color-primary;
    display: grid;
    place-items: center;
    cursor: pointer;
    opacity: 0;
    transform: translateY(8px) scale(0.8);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    transition:
      opacity 0.3s ease,
      transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1),
      background 0.2s ease,
      box-shadow 0.2s ease;
  }

  .hover-mask {
    display: none;
  }

  .info {
    flex: 0 0 auto;
    padding: 10px 12px 12px;
    min-height: 0;
    overflow: hidden;
  }

  .name {
    margin: 0 0 6px;
    font-size: 12px;
    font-weight: 500;
    line-height: 1.4;
    color: $color-text-primary;
    height: auto;
    max-height: 34px;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    word-break: normal;
    overflow-wrap: break-word;
    white-space: normal;
    transition: color 0.2s ease;
  }

  .price-row {
    margin: 0;
    line-height: 1.2;
    display: flex;
    align-items: baseline;
    gap: 4px;
  }

  .price-row .price {
    display: inline;
    font-size: 16px;
    font-weight: 700;
    line-height: 1.2;
    color: $color-primary;
    letter-spacing: 0;
    white-space: nowrap;
    transition: color 0.2s ease;
  }

  .price-row .symbol {
    font-size: 12px;
    margin-right: 1px;
    font-weight: 600;
  }

  .sale {
    margin: 4px 0 0;
    font-size: 11px;
    line-height: 1.2;
    color: $color-text-muted;
  }

  &:hover {
    border-color: rgba($color-gold, 0.3);
    box-shadow:
      0 12px 32px rgba(0, 0, 0, 0.12),
      0 4px 12px rgba(0, 0, 0, 0.06),
      0 0 0 1px rgba($color-gold, 0.1);
    transform: translateY(-6px) scale(1.02);

    .cover-wrap :deep(img) {
      transform: scale(1.08);
    }

    .cover-wrap {
      background: linear-gradient(180deg, #ffffff 0%, #fafafa 100%);
    }

    .name {
      color: $color-primary;
    }

    .price-row .price {
      color: $color-gold;
    }

    .cart-float {
      opacity: 1;
      transform: translateY(0) scale(1);
      box-shadow: 0 6px 20px rgba(201, 169, 98, 0.25);
    }
  }

  &:active {
    transform: translateY(-2px) scale(0.99);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
    transition-duration: 0.1s;
  }

  &:focus-visible {
    outline: 2px solid $color-gold;
    outline-offset: 3px;
  }
}

@media (max-width: $breakpoint-mobile) {
  .product-card:not(.is-compact) .hover-mask {
    display: none;
  }

  .product-card:not(.is-compact) .cart-float {
    opacity: 1;
    transform: scale(1);
  }
}
</style>
