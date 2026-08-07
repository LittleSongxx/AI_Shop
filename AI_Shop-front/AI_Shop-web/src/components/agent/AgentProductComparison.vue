<template>
  <section class="comparison-card" aria-label="商品比较">
    <header class="comparison-head">
      <div>
        <p class="comparison-kicker">实时商品快照</p>
        <h3>商品比较</h3>
      </div>
      <time v-if="card.generatedAt" :datetime="card.generatedAt">{{ formatTime(card.generatedAt) }}</time>
    </header>

    <div class="comparison-scroll">
      <table class="comparison-table">
        <thead>
          <tr>
            <th scope="col">维度</th>
            <th v-for="product in products" :key="product.productId" scope="col" class="product-col">
              <button type="button" class="product-heading" @click="openProduct(product.productId)">
                <ProductImage :product="product" :width="48" :height="48" />
                <span>{{ product.productName || product.productId }}</span>
              </button>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">价格</th>
            <td v-for="product in products" :key="`${product.productId}-price`">
              <strong>¥{{ money(product.minPrice) }}</strong>
              <span v-if="product.maxPrice && Number(product.maxPrice) > Number(product.minPrice)">
                - ¥{{ money(product.maxPrice) }}
              </span>
              <small v-if="product.priceChanged" class="changed">价格已变化</small>
            </td>
          </tr>
          <tr>
            <th scope="row">库存</th>
            <td v-for="product in products" :key="`${product.productId}-stock`">
              <span :class="availabilityClass(product.availability)">{{ availabilityText(product) }}</span>
            </td>
          </tr>
          <tr v-for="dimension in dimensions" :key="dimension">
            <th scope="row">{{ dimension }}</th>
            <td v-for="product in products" :key="`${product.productId}-${dimension}`">
              {{ propertyValue(product, dimension) || '—' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="comparison-foot">
      <span>信息来自实时商品快照，价格和库存以下单页为准。</span>
      <button type="button" class="refresh-hint" title="查看商品详情" @click="openProduct(products[0]?.productId)">
        查看详情
      </button>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import ProductImage from '@/components/common/ProductImage.vue';

type ComparisonProduct = Record<string, any> & { productId: string | number };
const props = defineProps<{ card: Record<string, any> }>();
const router = useRouter();

const products = computed<ComparisonProduct[]>(() =>
  (Array.isArray(props.card?.products) ? props.card.products : []).filter((item: any) => item?.productId)
);

const dimensions = computed(() => {
  const values = Array.isArray(props.card?.dimensions) ? props.card.dimensions : [];
  return values.filter((value: unknown) => {
    const text = String(value || '');
    return text && text !== '价格' && text !== '库存';
  }).slice(0, 10);
});

const money = (value: unknown) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(2) : '—';
};

const propertyValue = (product: ComparisonProduct, dimension: string) => {
  const property = (product.properties || []).find(
    (item: any) => String(item?.name || '') === dimension
  );
  return property?.value || '';
};

const availabilityText = (product: ComparisonProduct) => {
  if (product.availability === 'OUT_OF_STOCK') return '暂时缺货';
  if (product.availability === 'UNAVAILABLE') return '已下架';
  return '有货';
};

const availabilityClass = (availability: unknown) => ({
  available: availability === 'ON_SALE',
  unavailable: availability !== 'ON_SALE'
});

const formatTime = (value: unknown) => {
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value || '');
  return date.toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit' });
};

const openProduct = (productId: unknown) => {
  if (productId == null || productId === '') return;
  void router.push(`/product/${productId}`);
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.comparison-card {
  width: 100%;
  min-width: 0;
  overflow: hidden;
  border: 1px solid $color-border;
  border-radius: $radius-sm;
  background: #fff;
}

.comparison-head,
.comparison-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
}

.comparison-head {
  border-bottom: 1px solid $color-border-gray;

  h3 {
    margin: 2px 0 0;
    color: $color-text-title;
    font-size: 14px;
  }

  time {
    flex: 0 0 auto;
    color: $color-text-muted;
    font-size: 11px;
  }
}

.comparison-kicker {
  margin: 0;
  color: $color-primary;
  font-size: 11px;
}

.comparison-scroll {
  overflow-x: auto;
  overscroll-behavior-x: contain;
}

.comparison-table {
  width: 100%;
  min-width: 440px;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 12px;

  th,
  td {
    min-width: 112px;
    padding: 9px 8px;
    border-bottom: 1px solid $color-border-gray;
    text-align: left;
    vertical-align: top;
    word-break: break-word;
  }

  thead th:first-child,
  tbody th {
    width: 68px;
    min-width: 68px;
    color: $color-text-muted;
    font-weight: 500;
  }

  tbody td {
    color: $color-text-body;
  }
}

.product-heading {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: $color-text-title;
  font: inherit;
  text-align: left;
  cursor: pointer;

  span {
    display: -webkit-box;
    min-width: 0;
    overflow: hidden;
    line-height: 1.35;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }
}

.available { color: $color-success; }
.unavailable { color: $color-error; }
.changed {
  display: block;
  margin-top: 3px;
  color: $color-warning;
  font-size: 10px;
}

.comparison-foot {
  color: $color-text-muted;
  font-size: 11px;
  line-height: 1.4;

  .refresh-hint {
    flex: 0 0 auto;
    padding: 0;
    border: 0;
    background: transparent;
    color: $color-primary;
    font-size: 11px;
    cursor: pointer;
  }
}
</style>
