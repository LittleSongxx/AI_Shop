<template>
  <div class="agent-products">
    <RouterLink
      v-for="(item, index) in list"
      :key="item.productId || `p-${index}`"
      :to="`/product/${item.productId}`"
      class="product-link"
      @click="onProductClick(item)"
    >
      <ProductImage :product="item" :width="52" :height="52" />
      <p class="name">{{ item.productName }}</p>
      <p class="price">{{ formatPriceRange(item) }}</p>
      <p v-if="item.brand || item.inStock != null" class="meta">
        <span v-if="item.brand">{{ item.brand }}</span>
        <span v-if="item.inStock != null" :class="{ unavailable: item.inStock === false }">
          {{ item.inStock === false ? '暂时缺货' : '有货' }}
        </span>
      </p>
      <p v-if="item.reason" class="reason">{{ item.reason }}</p>
    </RouterLink>
    <p v-if="!list.length" class="empty">暂无相关商品</p>
  </div>
</template>

<script setup lang="ts">
import { RouterLink } from 'vue-router';
import ProductImage from '@/components/common/ProductImage.vue';
import { saveAgentConsultProduct } from '@/utils/agentProductConsult';
import { useAuthStore } from '@/stores/auth';

defineProps<{ list: Record<string, any>[] }>();

const authStore = useAuthStore();

const onProductClick = (item: Record<string, any>) => {
  if (!item?.productId || !item?.productName) return;
  saveAgentConsultProduct(
    {
      productId: String(item.productId),
      productName: String(item.productName),
      cover: item.cover ? String(item.cover) : undefined,
      minPrice: item.minPrice
    },
    authStore.userInfo?.userId as string | undefined
  );
};

const formatPrice = (val: unknown) => Number(val ?? 0).toFixed(2);

const formatPriceRange = (item: Record<string, any>) => {
  const min = Number(item.minPrice ?? item.price ?? 0);
  const max = item.maxPrice == null ? null : Number(item.maxPrice);
  if (max != null && Number.isFinite(max) && max > min) {
    return `¥${formatPrice(min)} - ¥${formatPrice(max)}`;
  }
  return `¥${formatPrice(min)}`;
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.agent-products {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  max-height: 280px;
  overflow-y: auto;
}

.product-link {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px;
  border: 1px solid $color-border;
  border-radius: $radius-sm;
  text-decoration: none;
  color: inherit;
  background: #fafafa;

  &:hover {
    border-color: rgba($color-primary, 0.4);
  }

  .name {
    margin: 0;
    font-size: 12px;
    line-height: 1.35;
    color: $color-text-title;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .price {
    margin: 0;
    font-size: 13px;
    font-weight: 600;
    color: $color-price;
  }

  .meta {
    display: flex;
    gap: 8px;
    justify-content: space-between;
    margin: 0;
    font-size: 11px;
    color: $color-text-muted;

    .unavailable {
      color: $color-error;
    }
  }

  .reason {
    margin: 0;
    overflow: hidden;
    color: $color-text-secondary;
    font-size: 11px;
    line-height: 1.3;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.empty {
  grid-column: 1 / -1;
  margin: 0;
  font-size: 12px;
  color: $color-text-muted;
  text-align: center;
}
</style>
