<template>
  <Teleport to="body">
    <Transition name="sku-sheet-fade">
      <div v-if="visible" class="sku-sheet-root" :class="{ 'is-desktop': isDesktop }" @click.self="close">
        <Transition name="sku-sheet-slide">
          <section v-if="visible" class="sku-sheet-panel ignore" role="dialog" aria-modal="true" aria-label="选择规格">
            <button type="button" class="sheet-close" aria-label="关闭" @click="close">
              <el-icon :size="20"><Close /></el-icon>
            </button>
            <div class="sheet-drag" aria-hidden="true" />

            <el-skeleton v-if="loading" animated :rows="6" class="sheet-skeleton" />

            <template v-else-if="productInfo">
              <header class="pick-header">
                <ProductImage
                  :source="coverImage"
                  width="56px"
                  height="56px"
                  fit="contain"
                  class="pick-cover"
                />
                <div class="pick-meta">
                  <h2 class="pick-name">{{ productInfo.productName }}</h2>
                  <p class="pick-price">
                    <span class="sym">¥</span>{{ displayPrice }}
                  </p>
                  <p class="pick-stock">
                    库存 {{ selectedSku?.stock ?? '--' }}
                    <em v-if="selectedSku?.stock != null && selectedSku.stock <= 5">紧张</em>
                  </p>
                </div>
              </header>

              <div class="pick-body">
                <div v-for="prop in productPropertyList" :key="prop.propertyId" class="sku-row">
                  <div class="sku-label">{{ prop.propertyName }}</div>
                  <div class="sku-values">
                    <button
                      v-for="val in prop.propertyValues"
                      :key="val.propertyValueId"
                      type="button"
                      class="sku-tag"
                      :class="{ active: selectedProperty[prop.propertyId] === val.propertyValueId }"
                      @click="selectProperty(prop, val)"
                    >
                      <ProductImage
                        v-if="val.propertyCover"
                        :source="val.propertyCover"
                        :width="18"
                        :height="18"
                        fit="contain"
                        :lazy="false"
                        dense
                        class="sku-thumb ignore"
                      />
                      <span class="sku-text">{{ val.propertyValue }}</span>
                    </button>
                  </div>
                </div>

                <div class="qty-row">
                  <span class="sku-label">数量</span>
                  <el-input-number v-model="quantity" :min="1" :max="maxBuy" size="small" />
                </div>
              </div>

              <footer class="pick-footer">
                <el-button type="primary" round size="large" class="btn-confirm" :loading="submitting" @click="confirmAdd">
                  加入购物车
                </el-button>
              </footer>
            </template>

            <el-empty v-else description="商品不存在或已下架" class="sheet-empty" />
          </section>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { onBeforeUnmount, watch, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Close } from '@element-plus/icons-vue';
import ProductImage from '@/components/common/ProductImage.vue';
import { cartApi } from '@/api/modules';
import { useProductSku } from '@/composables/useProductSku';
import { useProductSkuSheet } from '@/composables/useProductSkuSheet';
import { useAuthStore } from '@/stores/auth';
import { useCartStore } from '@/stores/cart';
import { toast } from '@/utils/toast';
import { useDevice } from '@/composables/useDevice';

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const cartStore = useCartStore();
const { isDesktop } = useDevice();
const { visible, productId, close } = useProductSkuSheet();
const submitting = ref(false);

const {
  loading,
  productInfo,
  productPropertyList,
  quantity,
  selectedSku,
  selectedProperty,
  displayPrice,
  maxBuy,
  coverImage,
  selectProperty,
  load,
  validateSku
} = useProductSku(() => productId.value);

watch(visible, (show) => {
  document.body.style.overflow = show ? 'hidden' : '';
  if (show && productId.value) {
    void load();
  }
});

onBeforeUnmount(() => {
  document.body.style.overflow = '';
});

const confirmAdd = async () => {
  if (!authStore.isLoggedIn) {
    close();
    router.push({ path: '/login', query: { redirect: route.fullPath } });
    return;
  }
  if (!validateSku() || !productInfo.value) return;

  submitting.value = true;
  try {
    await cartApi.add2Cart({
      productId: productInfo.value.productId,
      buyCount: quantity.value,
      propertyValueIds: selectedSku.value.propertyValueIds
    });
    await cartStore.fetchCartCount();
    toast.success('已加入购物车');
    close();
  } finally {
    submitting.value = false;
  }
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.sku-sheet-root {
  position: fixed;
  inset: 0;
  z-index: 1100;
  background: rgba(16, 18, 22, 0.45);
  display: flex;
  align-items: flex-end;
  justify-content: center;

  &.is-desktop {
    align-items: center;
  }
}

.sku-sheet-panel {
  position: relative;
  width: 100%;
  max-width: $content-width;
  max-height: min(78vh, 640px);
  display: flex;
  flex-direction: column;
  background: $color-card;
  border-radius: $radius-card $radius-card 0 0;
  box-shadow: 0 -12px 40px rgba(16, 24, 40, 0.18);
  padding-bottom: env(safe-area-inset-bottom, 0);
  margin-bottom: 50px;

  &::after {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    bottom: -50px;
    height: 50px;
    background: $color-card;
    z-index: -1;
  }

  .is-desktop & {
    border-radius: $radius-card;
    box-shadow: 0 12px 40px rgba(16, 24, 40, 0.18);
    margin-bottom: 0;
    max-width: 420px;

    &::after {
      display: none;
    }
  }
}

.sheet-close {
  position: absolute;
  top: 10px;
  right: 12px;
  z-index: 2;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: $color-bg-subtle;
  color: $color-text-muted;
  display: grid;
  place-items: center;
  cursor: pointer;

  &:active {
    background: $color-border-light;
  }
}

.sheet-drag {
  flex-shrink: 0;
  width: 36px;
  height: 4px;
  margin: 8px auto 4px;
  border-radius: $radius-xs;
  background: rgba($color-text-muted, 0.35);
}

.sheet-skeleton {
  padding: 16px;
}

.pick-header {
  display: flex;
  gap: 10px;
  padding: 4px 44px 12px 16px;
  flex-shrink: 0;
}

.pick-cover {
  flex-shrink: 0;
  border-radius: $radius-xs;
  overflow: hidden;
  background: $color-bg-subtle;
}

.pick-meta {
  flex: 1;
  min-width: 0;
}

.pick-name {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.35;
  color: $color-text-title;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.pick-price {
  margin: 0 0 2px;
  font-size: 18px;
  font-weight: 700;
  color: $color-price;

  .sym {
    font-size: 12px;
    font-weight: 600;
  }
}

.pick-stock {
  margin: 0;
  font-size: 11px;
  color: $color-text-muted;

  em {
    margin-left: 4px;
    font-style: normal;
    color: $color-price;
  }
}

.pick-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 16px 12px;
  -webkit-overflow-scrolling: touch;
}

.sku-row {
  margin-bottom: 14px;
}

.sku-label {
  font-size: 13px;
  font-weight: 600;
  color: $color-text-title;
  margin-bottom: 8px;
}

.sku-values {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.sku-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 11px;
  min-height: 30px;
  border: 1px solid $color-border;
  border-radius: $radius-btn;
  background: $color-bg-subtle;
  font-size: 12px;
  line-height: 1.35;
  color: $color-text-body;
  cursor: pointer;
  transition: border-color $transition-fast, background $transition-fast, color $transition-fast;

  &.active {
    border-color: $color-primary;
    background: $color-primary-soft;
    color: $color-primary;
  }
}

.sku-text {
  line-height: 1.35;
}

.sku-thumb {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  border-radius: $radius-xs;
  overflow: hidden;
  background: #fff;
  border: 1px solid rgba($color-border, 0.55);
  box-sizing: border-box;

  :deep(.product-image) {
    width: 18px !important;
    height: 18px !important;
    border-radius: $radius-xs;
  }
}

.qty-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 8px;
  border-top: 1px solid $color-border-light;
}

.pick-footer {
  flex-shrink: 0;
  padding: 10px 16px 12px;
  border-top: 1px solid $color-border-light;
  background: $color-card;
}

.btn-confirm {
  width: 100%;
  font-weight: 600;
}

.sheet-empty {
  padding: 32px 16px;
}

.sku-sheet-fade-enter-active,
.sku-sheet-fade-leave-active {
  transition: opacity 0.22s ease;
}

.sku-sheet-fade-enter-from,
.sku-sheet-fade-leave-to {
  opacity: 0;
}

.sku-sheet-slide-enter-active,
.sku-sheet-slide-leave-active {
  transition: transform 0.28s cubic-bezier(0.32, 0.72, 0, 1);
}

.sku-sheet-slide-enter-from,
.sku-sheet-slide-leave-to {
  transform: translateY(100%);
}

.is-desktop .sku-sheet-slide-enter-from,
.is-desktop .sku-sheet-slide-leave-to {
  transform: translateY(0) scale(0.95);
  opacity: 0;
}
</style>
