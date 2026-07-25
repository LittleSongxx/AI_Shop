<template>
  <div class="pc-footprint-page">
    <div class="pc-footprint-toolbar">
      <el-button v-if="list.length" size="small" plain @click="clearAll">清空</el-button>
    </div>

    <div v-if="list.length" class="pc-footprint-grid">
      <article
        v-for="row in list"
        :key="row.historyId"
        class="pc-footprint-card"
        @click="goDetail(row.productId)"
      >
        <div class="cover-col">
          <ProductImage :source="row.cover" class="cover" />
        </div>
        <div class="meta">
          <p class="name">{{ row.productName }}</p>
          <p class="price">¥{{ formatMoney(row.minPrice) }}</p>
        </div>
        <el-button
          class="btn-del"
          size="small"
          link
          type="danger"
          @click.stop="remove(row.historyId)"
        >
          删除
        </el-button>
      </article>
    </div>

    <el-empty v-else-if="!loading" description="暂无足迹，去逛逛吧" />

    <div ref="sentinelRef" class="load-sentinel" />
    <p v-if="loadingMore" class="load-tip">加载中…</p>
    <p v-else-if="finished && list.length" class="load-tip muted">没有更多了</p>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
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
const sentinelRef = ref<HTMLElement | null>(null);
let observer: IntersectionObserver | null = null;

const formatMoney = (val: unknown) => Number(val ?? 0).toFixed(2);

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

const remove = async (historyId: number) => {
  await browseApi.removeBrowse(historyId);
  list.value = list.value.filter((x) => x.historyId !== historyId);
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
  toast.success('已清空');
};

const reloadFromStart = async () => {
  list.value = [];
  pageNo.value = 0;
  pageTotal.value = 1;
  finished.value = false;
  await loadMore();
};

onMounted(async () => {
  await loadMore();
  setupObserver();
});

usePageRefresh(reloadFromStart);
onUnmounted(() => observer?.disconnect());
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-footprint-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}

.pc-footprint-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.pc-footprint-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border: 1px solid $color-border-gray;
  border-radius: $radius-sm;
  background: #fafafa;
  cursor: pointer;
  transition: border-color $transition-fast, box-shadow $transition-fast, background $transition-fast;

  &:hover {
    border-color: rgba($color-primary, 0.35);
    background: #fff;
    box-shadow: $shadow-card;
  }

  .cover-col {
    flex: 0 0 64px;
    width: 64px;
    height: 64px;
    border-radius: $radius-xs;
    overflow: hidden;
  }

  .meta {
    flex: 1;
    min-width: 0;

    .name {
      margin: 0 0 6px;
      font-size: 13px;
      line-height: 1.35;
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
  }

  .btn-del {
    flex-shrink: 0;
    font-size: 13px;
  }
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
