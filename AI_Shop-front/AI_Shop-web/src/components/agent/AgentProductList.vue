<template>
  <div class="agent-products">
    <article
      v-for="(item, index) in list"
      :key="item.productId || `p-${index}`"
      class="product-tile"
    >
      <button type="button" class="product-link" @click="onProductClick(item, index)">
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
      </button>
      <button
        type="button"
        class="compare-toggle"
        :class="{ selected: selectedIds.includes(String(item.productId)) }"
        :aria-pressed="selectedIds.includes(String(item.productId))"
        @click="toggleCompare(item)"
      >
        {{ selectedIds.includes(String(item.productId)) ? '已选比较' : '加入比较' }}
      </button>
    </article>
    <p v-if="!list.length" class="empty">暂无相关商品</p>
    <div v-if="selectedIds.length >= 2" class="compare-bar">
      <span>已选择 {{ selectedIds.length }} 个商品</span>
      <button type="button" class="compare-submit" @click="submitCompare">比较</button>
      <button type="button" class="compare-clear" aria-label="清空比较选择" @click="clearCompare">清空</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import ProductImage from '@/components/common/ProductImage.vue';
import { saveAgentConsultProduct } from '@/utils/agentProductConsult';
import { useAuthStore } from '@/stores/auth';
import { agentApi } from '@/api/modules';
import { saveRecommendationAttribution } from '@/utils/recommendationAttribution';
import { toast } from '@/utils/toast';

const props = defineProps<{ list: Record<string, any>[] }>();
const emit = defineEmits<{ 'compare-products': [productIds: string[]] }>();

const authStore = useAuthStore();
const router = useRouter();
const selectedIds = ref<string[]>([]);

const onProductClick = async (item: Record<string, any>, position = 0) => {
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
  const requestId = item.requestId ? String(item.requestId) : '';
  const userId = String(authStore.userInfo?.userId || '');
  if (requestId && userId) {
    try {
      const attribution = await agentApi.reportClick(
      String(item.productId),
      requestId,
      position + 1
      );
      saveRecommendationAttribution(attribution, userId);
    } catch {
      // Navigation is the primary action; attribution is best effort.
    }
  }
  await router.push(`/product/${item.productId}`);
};

const toggleCompare = (item: Record<string, any>) => {
  const id = String(item?.productId || '').trim();
  if (!id) return;
  if (selectedIds.value.includes(id)) {
    selectedIds.value = selectedIds.value.filter((value) => value !== id);
    return;
  }
  if (selectedIds.value.length >= 4) {
    toast.info('最多选择 4 个商品进行比较');
    return;
  }
  if (!props.list.some((candidate) => String(candidate?.productId) === id)) return;
  selectedIds.value = [...selectedIds.value, id];
};

const submitCompare = () => {
  emit('compare-products', [...selectedIds.value]);
};

const clearCompare = () => {
  selectedIds.value = [];
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

.product-tile {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 5px;
  padding: 0;
  border: 1px solid $color-border;
  border-radius: $radius-sm;
  background: #fafafa;
  overflow: hidden;
}

.product-link {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px;
  border: 0;
  border-radius: 0;
  text-decoration: none;
  color: inherit;
  background: #fafafa;
  text-align: left;
  cursor: pointer;
  font: inherit;

  &:hover {
    background: rgba($color-primary, 0.035);
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

.compare-toggle {
  align-self: flex-start;
  min-height: 26px;
  margin: 0 8px 8px;
  padding: 0 8px;
  border: 1px solid $color-border-gray;
  border-radius: $radius-pill;
  background: #fff;
  color: $color-text-muted;
  font-size: 11px;
  cursor: pointer;

  &.selected {
    color: $color-primary;
    border-color: rgba($color-primary, 0.42);
    background: $color-primary-soft;
  }
}

.compare-bar {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-top: 1px solid $color-border-gray;
  color: $color-text-muted;
  font-size: 12px;
  background: #fff;
}

.compare-submit,
.compare-clear {
  min-height: 26px;
  padding: 0 10px;
  border-radius: $radius-pill;
  font-size: 12px;
  cursor: pointer;
}

.compare-submit {
  margin-left: auto;
  border: 1px solid $color-primary;
  background: $color-primary;
  color: #fff;
}

.compare-clear {
  border: 1px solid $color-border-gray;
  background: #fff;
  color: $color-text-muted;
}

.empty {
  grid-column: 1 / -1;
  margin: 0;
  font-size: 12px;
  color: $color-text-muted;
  text-align: center;
}
</style>
