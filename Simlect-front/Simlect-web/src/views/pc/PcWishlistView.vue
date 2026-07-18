<template>
  <div class="pc-wishlist-page">
    <div class="pc-wishlist-toolbar">
      <el-button v-if="list.length" size="small" plain @click="editing = !editing">
        {{ editing ? '完成' : '管理' }}
      </el-button>
    </div>

    <div v-if="list.length" class="pc-wishlist-grid">
      <article v-for="row in list" :key="row.favoriteId" class="pc-wishlist-card">
        <button type="button" class="cover-btn" @click="goDetail(row.productId)">
          <ProductImage :source="row.cover" class="cover" />
        </button>
        <div class="meta">
          <p class="name">{{ row.productName }}</p>
          <p class="price">¥{{ formatMoney(row.minPrice) }}</p>
        </div>
        <el-button
          v-if="editing"
          class="btn-remove"
          size="small"
          type="danger"
          plain
          @click="remove(row.favoriteId)"
        >
          删除
        </el-button>
      </article>
    </div>

    <el-empty v-else-if="!loading" description="暂无收藏，去逛逛吧" />

    <div ref="sentinelRef" class="load-sentinel" />
    <p v-if="loadingMore" class="load-tip">加载中…</p>
    <p v-else-if="finished && list.length" class="load-tip muted">没有更多了</p>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import ProductImage from '@/components/common/ProductImage.vue';
import { favoriteApi } from '@/api/modules';
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
const editing = ref(false);
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
    const r = await favoriteApi.loadFavorite({ pageNo: next });
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

const remove = async (favoriteId: string) => {
  const ok = await confirmAction('确定要删除该收藏吗？', { title: '删除收藏', confirmButtonText: '删除' });
  if (!ok) return;
  await favoriteApi.removeFavorite(favoriteId);
  toast.success('已删除');
  list.value = [];
  pageNo.value = 0;
  pageTotal.value = 1;
  finished.value = false;
  await loadMore();
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

.pc-wishlist-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}

.pc-wishlist-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax($pc-product-tile-min-width, 1fr));
  gap: 10px;
  align-items: start;
}

.pc-wishlist-card {
  border: 1px solid $color-border-light;
  border-radius: $radius-sm;
  overflow: hidden;
  background: #fff;
  padding-bottom: 8px;
  transition: border-color $transition-fast, box-shadow $transition-fast, transform $transition-fast;

  &:hover {
    border-color: rgba($color-primary, 0.35);
    box-shadow: $shadow-card-hover;
    transform: translateY(-2px);

    .cover-btn :deep(img) {
      transform: scale(1.04);
    }
  }

  .cover-btn {
    width: 100%;
    padding: 0;
    border: none;
    background: #fafafa;
    aspect-ratio: 1;
    cursor: pointer;
    overflow: hidden;

    .cover {
      width: 100%;
      height: 100%;
    }

    :deep(img) {
      transition: transform $transition-normal;
    }
  }

  .meta {
    padding: 8px 8px 0;

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

    .price {
      margin: 0;
      font-size: 14px;
      font-weight: 700;
      color: $color-price;
    }
  }

  .btn-remove {
    display: block;
    margin: 8px auto 0;
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
