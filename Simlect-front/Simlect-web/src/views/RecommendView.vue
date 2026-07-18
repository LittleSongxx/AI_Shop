<template>
  <div class="recommend-page">
    <header class="page-header card">
      <h1 class="page-title">编辑精选</h1>
      <p class="page-subtitle">买手团队严选，品质之选</p>
    </header>

    <div class="card result-card">
      <div v-if="products.length" class="product-grid">
        <ProductCard
          v-for="item in products"
          :key="item.productId"
          :product="item"
          compact
          @click="goDetail"
        />
      </div>
      <div v-else-if="!loading" class="page-empty">
        <el-empty description="暂无推荐商品" />
      </div>
      <div ref="sentinelRef" class="load-sentinel" />
      <p v-if="loadingMore" class="load-tip">加载中…</p>
      <p v-else-if="finished && products.length" class="load-tip">已展示全部推荐商品</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import ProductCard from '@/components/business/ProductCard.vue';
import { productApi } from '@/api/modules';
import { filterOnSaleProducts } from '@/utils/product';

const router = useRouter();

const pageNo = ref(0);
const pageTotal = ref(1);
const products = ref<any[]>([]);
const loading = ref(false);
const loadingMore = ref(false);
const finished = ref(false);
const sentinelRef = ref<HTMLElement | null>(null);
let observer: IntersectionObserver | null = null;

const MAX_PRODUCTS = 60;

const setupObserver = () => {
  observer?.disconnect();
  if (!sentinelRef.value) return;
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) loadMore();
    },
    { rootMargin: '120px' }
  );
  observer.observe(sentinelRef.value);
};

const loadMore = async () => {
  if (loadingMore.value || finished.value) return;
  if (products.value.length >= MAX_PRODUCTS) {
    finished.value = true;
    return;
  }
  if (pageNo.value >= pageTotal.value && pageNo.value > 0) {
    finished.value = true;
    return;
  }

  loadingMore.value = true;
  if (!products.value.length) loading.value = true;
  try {
    const next = pageNo.value + 1;
    const r = await productApi.loadCommendProduct();
    const list = Array.isArray(r) ? r : r?.list || [];
    const chunk = filterOnSaleProducts(list);

    if (next === 1) products.value = chunk;
    else products.value = products.value.concat(chunk);

    pageNo.value = next;
    pageTotal.value = Math.ceil(MAX_PRODUCTS / 10);

    if (products.value.length >= MAX_PRODUCTS) {
      finished.value = true;
    }
  } catch (error) {
    console.error('RecommendView: loadMore error', error);
    finished.value = true;
  } finally {
    loadingMore.value = false;
    loading.value = false;
  }
};

const goDetail = (p: any) => router.push(`/product/${p.productId}`);

onMounted(() => {
  loadMore();
  setupObserver();
});

onUnmounted(() => observer?.disconnect());
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.recommend-page {
  padding-bottom: $mobile-tab-reserved;
}

.page-header {
  margin: 0;
  padding: 16px;
  background: linear-gradient(135deg, $color-gold-soft 0%, rgba($color-gold, 0.06) 100%);
  border: 1px solid rgba($color-gold, 0.15);

  .page-title {
    margin: 0 0 4px;
    font-size: 18px;
    font-weight: 600;
    color: $color-text-title;
  }

  .page-subtitle {
    margin: 0;
    font-size: 13px;
    color: $color-text-muted;
  }
}

.result-card {
  margin-top: 12px;
  padding: 12px;
}

.product-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.load-sentinel {
  height: 1px;
}

.load-tip {
  text-align: center;
  font-size: 12px;
  color: $color-text-muted;
  padding: 12px 0;
}

.page-empty {
  padding: 40px 0;
}
</style>