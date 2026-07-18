<template>
  <div class="footprint-page">
    <div ref="scrollRoot" class="footprint-scroll">
      <header class="footprint-head">
        <h2 class="title">我的足迹</h2>
        <el-button v-if="list.length" size="small" plain @click="clearAll">清空</el-button>
      </header>

      <div v-if="list.length" class="list">
        <SwipeDeleteRow
          v-for="row in list"
          :key="row.historyId"
          :open="openSwipeId === row.historyId"
          @open="openSwipeId = row.historyId"
          @close="onSwipeClose(row.historyId)"
          @delete="remove(row.historyId)"
        >
          <article class="footprint-item" @click="goDetail(row.productId)">
            <div class="cover-col">
              <ProductImage :source="row.cover" class="cover" />
            </div>
            <div class="meta">
              <p class="name">{{ row.productName }}</p>
              <p class="sub">
                <span class="price">¥{{ formatMoney(row.minPrice) }}</span>
              </p>
            </div>
          </article>
        </SwipeDeleteRow>
      </div>

      <el-empty v-else-if="!loading" description="暂无足迹，去逛逛吧" />

      <div ref="sentinelRef" class="load-sentinel" />
      <p v-if="loadingMore" class="load-tip">加载中…</p>
      <p v-else-if="finished && list.length" class="load-tip muted">没有更多了</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import SwipeDeleteRow from '@/components/business/SwipeDeleteRow.vue';
import ProductImage from '@/components/common/ProductImage.vue';
import { browseApi } from '@/api/modules';
import { confirmAction } from '@/utils/confirm';
import { toast } from '@/utils/toast';
import { usePageRefresh } from '@/composables/pullRefresh';

const router = useRouter();
const list = ref<any[]>([]);
const pageNo = ref(0);
const pageTotal = ref(1);
const loading = ref(false);
const loadingMore = ref(false);
const finished = ref(false);
const openSwipeId = ref<number | null>(null);

const scrollRoot = ref<HTMLElement>();
const sentinelRef = ref<HTMLElement>();
let observer: IntersectionObserver | null = null;

const formatMoney = (val: unknown) => Number(val ?? 0).toFixed(2);

const setupObserver = () => {
  observer?.disconnect();
  if (!sentinelRef.value) return;
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) loadMore();
    },
    { root: scrollRoot.value, rootMargin: '120px' }
  );
  observer.observe(sentinelRef.value);
};

const loadMore = async () => {
  if (loadingMore.value || finished.value) return;
  if (pageNo.value >= pageTotal.value && pageNo.value > 0) {
    finished.value = true;
    return;
  }
  loadingMore.value = true;
  if (!list.value.length) loading.value = true;
  try {
    const next = pageNo.value + 1;
    const r = await browseApi.loadBrowse({ pageNo: next });
    const chunk = r?.list || [];
    if (next === 1) list.value = chunk;
    else list.value = list.value.concat(chunk);
    pageNo.value = r?.pageNo ?? next;
    pageTotal.value = r?.pageTotal ?? pageNo.value;
    finished.value = pageNo.value >= pageTotal.value;
  } finally {
    loadingMore.value = false;
    loading.value = false;
  }
};

const goDetail = (productId: string) => {
  if (productId) router.push(`/product/${productId}`);
};

const onSwipeClose = (id: number) => {
  if (openSwipeId.value === id) openSwipeId.value = null;
};

const remove = async (historyId: number) => {
  await browseApi.removeBrowse(historyId);
  list.value = list.value.filter((x) => x.historyId !== historyId);
  if (openSwipeId.value === historyId) openSwipeId.value = null;
  toast.success('已删除');
};

const clearAll = async () => {
  const ok = await confirmAction('确定清空全部足迹吗？', { title: '清空足迹', confirmButtonText: '清空' });
  if (!ok) return;
  await browseApi.clearBrowse();
  list.value = [];
  pageNo.value = 0;
  pageTotal.value = 1;
  finished.value = true;
  openSwipeId.value = null;
  toast.success('已清空');
};

const reloadFromStart = async () => {
  list.value = [];
  pageNo.value = 0;
  pageTotal.value = 1;
  finished.value = false;
  if (scrollRoot.value) scrollRoot.value.scrollTop = 0;
  await loadMore();
};

onMounted(async () => {
  await loadMore();
  setupObserver();
});

usePageRefresh(reloadFromStart, { getScrollEl: () => scrollRoot.value });

onUnmounted(() => observer?.disconnect());
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.footprint-scroll {
  height: 100%;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  padding: 12px $app-page-gutter $mobile-tab-reserved;
}

.footprint-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;

  .title {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
    color: $color-text-title;
  }
}

.list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.footprint-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 12px 12px 12px 8px;
  box-sizing: border-box;
  background: transparent;
}

.cover-col {
  flex: 0 0 56px;
  width: 56px;
  height: 56px;
  border-radius: $radius-sm;
  overflow: hidden;
}

.cover {
  width: 56px;
  height: 56px;
  border-radius: $radius-sm;

  :deep(.product-image) {
    width: 56px !important;
    height: 56px !important;
    min-height: 56px !important;
    border-radius: $radius-sm !important;
  }
}

.meta {
  flex: 1;
  min-width: 0;

  .name {
    margin: 0 0 4px;
    font-size: 13px;
    line-height: 1.35;
    color: $color-text-title;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .sub {
    margin: 0;
  }

  .price {
    margin: 0;
    font-size: 15px;
    font-weight: 700;
    color: $color-price;
  }
}

.load-sentinel {
  height: 1px;
}

.load-tip {
  margin: 10px 0 0;
  text-align: center;
  font-size: 12px;
  color: $color-text-muted;
}

.load-tip.muted {
  opacity: 0.8;
}
</style>