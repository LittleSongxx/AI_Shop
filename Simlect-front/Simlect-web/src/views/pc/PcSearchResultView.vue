<template>
  <div class="pc-search-result">
    <section class="filter-panel">
      <div class="search-row">
        <el-input v-model="query.keyWords" placeholder="搜索关键词" clearable @keyup.enter="onSearch" class="search-input">
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
          <template #append>
            <el-button @click="onSearch">搜索</el-button>
          </template>
        </el-input>
      </div>
      <div class="filter-row">
        <div class="filter-group">
          <span class="filter-label">价格</span>
          <el-input v-model="query.priceFrom" placeholder="最低价" class="price-input" />
          <span class="sep">—</span>
          <el-input v-model="query.priceTo" placeholder="最高价" class="price-input" />
        </div>
        <div class="filter-group">
          <span class="filter-label">排序</span>
          <el-select v-model="sortMode" placeholder="排序" clearable class="sort-select" @change="onSortChange">
            <el-option label="综合" value="" />
            <el-option label="价格从低到高" value="price-asc" />
            <el-option label="价格从高到低" value="price-desc" />
            <el-option label="销量" value="sale" />
          </el-select>
        </div>
      </div>
    </section>

    <section class="result-panel">
      <h3 class="result-title">「{{ activeKeywords }}」共 {{ total }} 件</h3>
      <div v-if="list.length" class="pc-result-grid">
        <PcProductTile
          v-for="p in list"
          :key="p.productId"
          :product="p"
          @click="goDetail"
        />
      </div>
      <el-empty v-else-if="!loading" description="暂无搜索结果">
        <el-button type="primary" @click="router.push('/')">去首页</el-button>
      </el-empty>
      <div ref="sentinelRef" class="load-sentinel" />
      <p v-if="loadingMore" class="load-tip">加载中…</p>
      <p v-else-if="finished && list.length" class="load-tip">没有更多了</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Search } from '@element-plus/icons-vue';
import PcProductTile from '@/components/pc/PcProductTile.vue';
import { usePageListCache } from '@/composables/usePageListCache';
import { productApi } from '@/api/modules';
import { useSearchStore } from '@/stores/search';
import { filterOnSaleProducts } from '@/utils/product';
import { toast } from '@/utils/toast';
import { usePageRefresh } from '@/composables/pullRefresh';

const router = useRouter();
const route = useRoute();
const searchStore = useSearchStore();

const resolveKeywords = () => {
  const fromQuery = typeof route.query.q === 'string' ? route.query.q.trim() : '';
  if (fromQuery) return fromQuery;
  return searchStore.payload.keyWords.trim();
};

const pageNo = ref(0);
const pageTotal = ref(1);
const total = ref(0);
const list = ref<any[]>([]);
const loading = ref(false);
const loadingMore = ref(false);
const finished = ref(false);
const sentinelRef = ref<HTMLElement | null>(null);
let observer: IntersectionObserver | null = null;
const activeKeywords = ref('');

type SortMode = '' | 'price-asc' | 'price-desc' | 'sale';

const query = reactive({
  keyWords: searchStore.payload.keyWords,
  priceFrom: searchStore.payload.priceFrom,
  priceTo: searchStore.payload.priceTo,
  sortType: searchStore.payload.sortType,
  sortField: searchStore.payload.sortField
});

const resolveSortMode = (): SortMode => {
  if (query.sortField === 'price') {
    return query.sortType === 'asc' ? 'price-asc' : 'price-desc';
  }
  if (query.sortField === 'sale') return 'sale';
  return '';
};

const applySortMode = (mode: SortMode) => {
  if (mode === 'price-asc') {
    query.sortField = 'price';
    query.sortType = 'asc';
  } else if (mode === 'price-desc') {
    query.sortField = 'price';
    query.sortType = 'desc';
  } else if (mode === 'sale') {
    query.sortField = 'sale';
    query.sortType = '';
  } else {
    query.sortField = '';
    query.sortType = '';
  }
};

const sortMode = computed<SortMode>({
  get: resolveSortMode,
  set: applySortMode
});

const cacheKey = () =>
  [
    '/search-result|pc',
    query.keyWords.trim(),
    query.priceFrom,
    query.priceTo,
    query.sortField,
    query.sortType,
    searchStore.payload.categoryId
  ].join('|');

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

const pageCache = usePageListCache({
  cacheKey,
  getState: () => ({
    query: { ...query },
    list: list.value,
    pageNo: pageNo.value,
    pageTotal: pageTotal.value,
    total: total.value,
    finished: finished.value,
    activeKeywords: activeKeywords.value
  }),
  setState: (state) => {
    const q = state.query as typeof query;
    if (q) Object.assign(query, q);
    list.value = (state.list as any[]) || [];
    pageNo.value = Number(state.pageNo) || 0;
    pageTotal.value = Number(state.pageTotal) || 1;
    total.value = Number(state.total) || 0;
    finished.value = !!state.finished;
    activeKeywords.value = String(state.activeKeywords ?? '');
    loading.value = false;
    loadingMore.value = false;
  },
  afterRestore: setupObserver
});

const loadMore = async () => {
  const keyWords = query.keyWords.trim();
  if (!keyWords) return;
  if (loadingMore.value || finished.value) return;
  if (pageNo.value >= pageTotal.value && pageNo.value > 0) {
    finished.value = true;
    return;
  }

  loadingMore.value = true;
  if (!list.value.length) loading.value = true;
  activeKeywords.value = keyWords;
  try {
    const next = pageNo.value + 1;
    const r = await productApi.search({
      keyWords,
      pageNo: next,
      categoryId: searchStore.payload.categoryId || undefined,
      priceFrom: query.priceFrom || undefined,
      priceTo: query.priceTo || undefined,
      sortType: query.sortType || undefined,
      sortField: query.sortField || undefined
    });
    const chunk = filterOnSaleProducts(r?.list);
    if (next === 1) list.value = chunk;
    else list.value = list.value.concat(chunk);
    pageNo.value = r?.pageNo ?? next;
    pageTotal.value = r?.pageTotal ?? pageNo.value;
    total.value = r?.totalCount ?? list.value.length;
    finished.value = pageNo.value >= pageTotal.value;
  } finally {
    loadingMore.value = false;
    loading.value = false;
  }
};

const resetAndLoad = () => {
  pageNo.value = 0;
  pageTotal.value = 1;
  finished.value = false;
  list.value = [];
  total.value = 0;
  window.scrollTo(0, 0);
  loadMore();
};

const onSortChange = () => {
  if (!query.keyWords.trim()) return;
  pageCache.clear();
  searchStore.setSearch({
    keyWords: query.keyWords.trim(),
    categoryId: searchStore.payload.categoryId,
    priceFrom: query.priceFrom,
    priceTo: query.priceTo,
    sortType: query.sortField === 'price' ? query.sortType : '',
    sortField: query.sortField
  });
  resetAndLoad();
};

const onSearch = () => {
  pageCache.clear();
  const keyWords = query.keyWords.trim();
  if (!keyWords) {
    toast.warning('请输入搜索关键词');
    return;
  }
  if (query.sortField === 'price' && !query.sortType) {
    query.sortType = 'desc';
  }
  searchStore.setSearch({
    keyWords,
    categoryId: searchStore.payload.categoryId,
    priceFrom: query.priceFrom,
    priceTo: query.priceTo,
    sortType: query.sortField === 'price' ? query.sortType : '',
    sortField: query.sortField
  });
  if (route.query.q !== keyWords) {
    router.replace({ path: '/search-result', query: { q: keyWords } });
  }
  resetAndLoad();
};

const goDetail = (p: any) => router.push(`/product/${p.productId}`);

const applySearch = (keyWords: string) => {
  searchStore.setSearch({
    ...searchStore.payload,
    keyWords
  });
  query.keyWords = keyWords;
  activeKeywords.value = keyWords;
  resetAndLoad();
};

onMounted(async () => {
  const keyWords = resolveKeywords();
  if (!keyWords) {
    router.replace('/search-portal');
    return;
  }
  Object.assign(query, searchStore.payload);
  query.keyWords = keyWords;

  const restored = await pageCache.tryRestore();
  if (!restored) {
    applySearch(keyWords);
  } else {
    activeKeywords.value = query.keyWords.trim();
  }
  setupObserver();
});

watch(
  () => route.query.q,
  (q) => {
    if (typeof q !== 'string') return;
    const keyWords = q.trim();
    if (!keyWords || keyWords === query.keyWords.trim()) return;
    applySearch(keyWords);
  }
);

usePageRefresh(() => {
  pageCache.clear();
  resetAndLoad();
});

onUnmounted(() => observer?.disconnect());
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-search-result {
  display: flex;
  flex-direction: column;
  gap: 16px;
  animation: fadeSlideUp 0.6s cubic-bezier(0.25, 0.1, 0.25, 1) both;
}

@keyframes fadeSlideUp {
  from {
    opacity: 0;
    transform: translateY(20px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.filter-panel {
  border: 1px solid $color-border;
  border-radius: $radius-card;
  background: $color-card;
  padding: 20px 24px;
  box-shadow: $shadow-card;
  transition: box-shadow $transition-normal;
}

.search-row {
  margin-bottom: 16px;
}

.search-input {
  :deep(.el-input-group__append) {
    .el-button {
      font-weight: 500;
    }
  }
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 20px;
}

.filter-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.filter-label {
  font-size: 13px;
  color: $color-text-muted;
  font-weight: 500;
  flex-shrink: 0;
}

.price-input {
  width: 100px;

  :deep(.el-input__wrapper) {
    border-radius: $radius-xs;
  }
}

.sort-select {
  width: 140px;

  :deep(.el-input__wrapper) {
    border-radius: $radius-xs;
  }
}

.sep {
  color: $color-text-muted;
  font-size: 13px;
}

.result-panel {
  border: 1px solid $color-border;
  border-radius: $radius-card;
  background: $color-card;
  padding: 20px 24px;
  box-shadow: $shadow-card;
}

.result-title {
  margin: 0 0 16px;
  font-size: 15px;
  font-weight: 600;
  color: $color-text-primary;
  letter-spacing: $letter-spacing-body;
}

.pc-result-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax($pc-product-tile-min-width, 1fr));
  gap: 14px;
  align-items: start;
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
</style>
