<template>
  <div class="comments-page product-page-compact card">
    <div class="card-section-title">
      <h3>全部评价</h3>
      <span class="count">共 {{ displayTotal }} 条</span>
    </div>

    <div class="level-tabs toolbar-row toolbar-row--chips">
      <button
        v-for="tab in levelTabs"
        :key="tab.value"
        type="button"
        class="toolbar-chip"
        :class="{ active: commentLevel === tab.value }"
        @click="commentLevel = tab.value"
      >
        {{ tab.label }}
      </button>
    </div>

    <div ref="scrollRoot" class="comment-scroll">
      <div v-if="displayList.length" class="comment-list">
        <article v-for="c in displayList" :key="c.orderId" class="comment-item">
          <UserAvatar :avatar="c.avatar" :size="40" />
          <div class="comment-body">
            <div class="comment-head">
              <span class="user">{{ maskCommenterName(c.nickName) }}</span>
              <span v-if="getLevelBadge(c.userId)" class="cmt-level-tag" :class="levelClass(getLevelBadge(c.userId)!.levelCode)">
                {{ getLevelBadge(c.userId)!.levelName }}
              </span>
              <el-rate v-if="c.star" :model-value="c.star" disabled size="small" />
              <button v-if="currentUserId !== c.userId" type="button" class="report-btn" title="举报" @click="reportComment(c)">举报</button>
            </div>
            <p v-if="c.propertyInfo" class="sku">已购：{{ c.propertyInfo }}</p>
            <p class="content">{{ c.commentContent }}</p>
            <div v-if="commentImages(c.commentImages).length" class="img-row">
              <ProductImage
                v-for="(img, idx) in commentImages(c.commentImages)"
                :key="`${c.orderId}-c-${idx}`"
                :source="img"
                width="72"
                height="72"
                :use-thumbnail="true"
                class="comment-img"
                @click="openCommentImagePreview(commentImages(c.commentImages), idx)"
              />
            </div>
            <p v-if="c.commentTime" class="time">{{ c.commentTime }}</p>
            <div v-if="c.commentBizReply" class="biz-reply">商家回复：{{ c.commentBizReply }}</div>
            <div v-if="c.recommentContent" class="re-comment">
              <span class="re-tag">{{ recommentLabel(c.commentTime, c.recommentTime) }}追评</span>
              {{ c.recommentContent }}
            </div>
            <div v-if="commentImages(c.recommentImages).length" class="img-row">
              <ProductImage
                v-for="(img, idx) in commentImages(c.recommentImages)"
                :key="`${c.orderId}-r-${idx}`"
                :source="img"
                width="72"
                height="72"
                :use-thumbnail="true"
                class="comment-img"
                @click="openCommentImagePreview(commentImages(c.recommentImages), idx)"
              />
            </div>
          </div>
        </article>
      </div>
      <el-empty v-else-if="!loading && finished" description="暂无评价" />

      <div ref="sentinelRef" class="load-sentinel" />
      <p v-if="loading" class="load-tip">加载中…</p>
      <p v-else-if="finished && rawList.length" class="load-tip muted">没有更多了</p>
    </div>
    <CommentReportDialog ref="reportRef" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { useAuthStore } from '@/stores/auth';
import { commentApi, userMemberApi } from '@/api/modules';
import { usePageRefresh } from '@/composables/pullRefresh';
import UserAvatar from '@/components/common/UserAvatar.vue';
import ProductImage from '@/components/common/ProductImage.vue';
import CommentReportDialog from '@/components/business/CommentReportDialog.vue';
import { usePageListCache } from '@/composables/usePageListCache';
import { matchCommentLevel, maskCommenterName, type CommentLevel } from '@/utils/comment';
import { openImagePreview } from '@/composables/imagePreview';
import { resolveImageUrl, splitImagePaths } from '@/utils/image';

const levelCache = ref<Map<string, { levelCode: number; levelName: string }>>(new Map());

const fetchLevelBadge = (userId: string) => {
  if (!userId || levelCache.value.has(userId)) return;
  userMemberApi.getLevelBadge(userId).then((res: any) => {
    if (res?.levelCode != null) {
      levelCache.value.set(userId, { levelCode: res.levelCode, levelName: res.levelName });

      levelCache.value = new Map(levelCache.value);
    }
  }).catch(() => {});
};

const getLevelBadge = (userId: string) => levelCache.value.get(userId) ?? null;

const levelClass = (code: number) => {
  if (code >= 3) return 'level-gold';
  if (code >= 2) return 'level-silver';
  return 'level-default';
};

const levelTabs: { label: string; value: CommentLevel }[] = [
  { label: '全部', value: '' },
  { label: '好评', value: 'good' },
  { label: '中评', value: 'medium' },
  { label: '差评', value: 'bad' }
];

const route = useRoute();
const auth = useAuthStore();
const currentUserId = computed(() => auth.userInfo?.userId ?? '');
const commentLevel = ref<CommentLevel>('');
const pageNo = ref(0);
const pageTotal = ref(1);
const apiTotal = ref(0);
const rawList = ref<any[]>([]);
const loading = ref(false);
const finished = ref(false);
const scrollRoot = ref<HTMLElement>();
const sentinelRef = ref<HTMLElement>();
let observer: IntersectionObserver | null = null;

const productId = () => String(route.params.productId);

const reportRef = ref<InstanceType<typeof CommentReportDialog>>();
const reportComment = (c: any) => {
  reportRef.value?.show({
    orderId: String(c.orderId),
    productId: productId(),
    commentContent: c.commentContent
  });
};

const displayList = computed(() =>
  rawList.value.filter((c) => matchCommentLevel(c.star, commentLevel.value))
);

const displayTotal = computed(() => {
  if (!commentLevel.value) return apiTotal.value;
  return displayList.value.length;
});

const commentImages = (raw?: string | null) => splitImagePaths(raw);

const openCommentImagePreview = (images: string[], index: number) => {

  const fullUrls = images.map((img) => resolveImageUrl(img, { useThumbnail: false }) || img);
  openImagePreview(fullUrls, index);
};

const recommentLabel = (commentTime?: string, recommentTime?: string) => {
  if (!commentTime || !recommentTime) return '';
  const start = new Date(commentTime.replace(/-/g, '/')).getTime();
  const end = new Date(recommentTime.replace(/-/g, '/')).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) return '';
  const days = Math.floor((end - start) / 86400000);
  if (days <= 0) return '当天';
  return `${days}天后`;
};

const setupObserver = () => {
  observer?.disconnect();
  if (!sentinelRef.value) return;
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) loadMore();
    },
    { root: scrollRoot.value, rootMargin: '80px', threshold: 0 }
  );
  observer.observe(sentinelRef.value);
};

const pageCache = usePageListCache({
  cacheKey: () => `/product/${productId()}/comments|${commentLevel.value}`,
  scrollRef: scrollRoot,
  getState: () => ({
    commentLevel: commentLevel.value,
    rawList: rawList.value,
    pageNo: pageNo.value,
    pageTotal: pageTotal.value,
    apiTotal: apiTotal.value,
    finished: finished.value
  }),
  setState: (state) => {
    commentLevel.value = (state.commentLevel as CommentLevel) ?? '';
    rawList.value = (state.rawList as any[]) || [];
    pageNo.value = Number(state.pageNo) || 0;
    pageTotal.value = Number(state.pageTotal) || 1;
    apiTotal.value = Number(state.apiTotal) || 0;
    finished.value = !!state.finished;
    loading.value = false;
  },
  afterRestore: setupObserver
});

const loadMore = async () => {
  if (loading.value || finished.value) return;
  if (pageNo.value >= pageTotal.value && pageNo.value > 0) {
    finished.value = true;
    return;
  }

  loading.value = true;
  try {
    const nextPage = pageNo.value + 1;
    const r = await commentApi.loadComment({ pageNo: nextPage, productId: productId() });
    const chunk = r?.list || [];
    if (nextPage === 1) rawList.value = chunk;
    else rawList.value = rawList.value.concat(chunk);

    pageNo.value = r?.pageNo ?? nextPage;
    pageTotal.value = r?.pageTotal ?? pageNo.value;
    apiTotal.value = r?.totalCount ?? rawList.value.length;
    finished.value = pageNo.value >= pageTotal.value;
  } finally {
    loading.value = false;
  }
};

const resetAndLoad = async () => {
  pageNo.value = 0;
  pageTotal.value = 1;
  finished.value = false;
  rawList.value = [];
  apiTotal.value = 0;
  if (scrollRoot.value) scrollRoot.value.scrollTop = 0;
  await loadMore();
};

watch(commentLevel, async () => {
  pageCache.clear();
  if (scrollRoot.value) scrollRoot.value.scrollTop = 0;
  await resetAndLoad();
  setupObserver();
});

watch(rawList, () => {
  rawList.value.forEach((c: any) => {
    if (c.userId) fetchLevelBadge(c.userId);
  });
}, { deep: false });

onMounted(async () => {
  const restored = await pageCache.tryRestore();
  if (!restored) {
    await resetAndLoad();
  }
  setupObserver();
});

usePageRefresh(async () => {
  pageCache.clear();
  await resetAndLoad();
  setupObserver();
}, { getScrollEl: () => scrollRoot.value });

onUnmounted(() => observer?.disconnect());
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.count {
  font-size: 12px;
  color: $color-text-muted;
  font-weight: 400;
}

.level-tabs {
  margin-bottom: 12px;
}

.comment-scroll {
  max-height: calc(100vh - 200px);
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

.comment-list {
  display: flex;
  flex-direction: column;
}

.comment-item {
  display: flex;
  gap: 10px;
  padding: 14px 0;
  border-bottom: 1px solid $color-border;

  &:last-child {
    border-bottom: none;
  }
}

.comment-body {
  flex: 1;
  min-width: 0;
}

.comment-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  flex-wrap: wrap;

  .user {
    font-size: 13px;
    font-weight: 600;
    color: $color-text-title;
  }

  .cmt-level-tag {
    flex-shrink: 0;
    padding: 1px 6px;
    border-radius: $radius-pill;
    font-size: 10px;
    font-weight: 600;
    line-height: 1.6;

    &.level-default {
      background: $color-bg-subtle;
      color: $color-text-muted;
      border: 1px solid $color-border;
    }

    &.level-silver {
      background: linear-gradient(135deg, #d0d0d0, #b0b0b5);
      color: #fff;
      border: 1px solid #a8a8ad;
    }

    &.level-gold {
      background: $color-accent-gradient-gold;
      color: #fff;
      border: 1px solid $color-gold;
    }
  }

  .report-btn {
    margin-left: auto;
    flex-shrink: 0;
    border: none;
    background: transparent;
    color: $color-text-muted;
    font-size: 12px;
    cursor: pointer;
    transition: color $transition-fast;

    &:hover {
      color: $color-price;
    }
  }
}

.sku {
  margin: 0 0 6px;
  font-size: 12px;
  color: $color-text-muted;
  line-height: 1.4;
}

.content {
  margin: 0 0 8px;
  font-size: 14px;
  line-height: 1.5;
  color: $color-text-body;
}

.img-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;

  .comment-img {
    border-radius: $radius-xs;
    overflow: hidden;
    cursor: pointer;
    transition: transform $transition-fast, box-shadow $transition-fast;

    &:hover {
      transform: scale(1.05);
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    }
  }
}

.time {
  margin: 0;
  font-size: 12px;
  color: $color-text-muted;
}

.biz-reply {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid $color-border;
  font-size: 12px;
  color: $color-text-muted;
  line-height: 1.5;
}

.re-comment {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid $color-border;
  font-size: 13px;
  line-height: 1.5;
  color: $color-text-body;

  .re-tag {
    color: $color-primary;
    margin-right: 4px;
    font-weight: 500;
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

  &.muted {
    opacity: 0.8;
  }
}
</style>
