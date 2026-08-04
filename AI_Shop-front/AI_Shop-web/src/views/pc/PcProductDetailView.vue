<template>
  <div v-if="productInfo" class="pc-product-detail ignore">
    <section class="pc-detail-main">
      <div class="pc-detail-gallery">
        <div v-if="galleryImages.length > 1" class="thumb-strip" role="tablist" aria-label="商品图">
          <button
            v-for="(img, i) in galleryImages"
            :key="i"
            type="button"
            class="thumb-btn"
            :class="{ active: activeImageIndex === i }"
            :aria-selected="activeImageIndex === i"
            @mouseenter="selectGalleryIndex(i)"
            @click="selectGalleryIndex(i)"
          >
            <ProductImage :source="img" width="56" height="56" fit="cover" dense />
          </button>
        </div>
        <div class="main-image-wrap" @click="openGalleryPreview(activeImageIndex)">
          <ProductImage
            v-if="galleryImages.length"
            :source="galleryImages[activeImageIndex]"
            :use-thumbnail="false"
            width="100%"
            height="100%"
            fit="contain"
            class="main-image"
          />
          <div v-else class="main-image-empty">
            <ProductImage width="100%" height="100%" />
          </div>
          <span v-if="galleryImages.length > 1" class="image-index">
            {{ activeImageIndex + 1 }} / {{ galleryImages.length }}
          </span>
        </div>
      </div>

      <div class="pc-detail-info">
        <h1 class="product-title">{{ productInfo.productName }}</h1>

        <div class="price-panel">
          <div class="price-row">
            <span class="price-label">促销价</span>
            <span class="price-value">
              <em>¥</em>{{ displayPrice }}
            </span>
          </div>
          <p class="sales-meta">销量 {{ productInfo.totalSale ?? 0 }} 件</p>
          <AgentServiceEntry
            class="price-agent-btn"
            :compact="false"
            show-label
            :icon-size="16"
            :consult-product="agentConsultProduct"
          />
        </div>

        <div class="sku-panel">
          <div v-for="prop in productPropertyList" :key="prop.propertyId" class="sku-line">
            <span class="sku-name">{{ prop.propertyName }}</span>
            <div class="sku-options">
              <button
                v-for="val in prop.propertyValues"
                :key="val.propertyValueId"
                type="button"
                class="sku-option"
                :class="{ active: selectedProperty[prop.propertyId] === val.propertyValueId }"
                @click="selectProperty(prop, val)"
              >
                <ProductImage
                  v-if="val.propertyCover"
                  :source="val.propertyCover"
                  :width="32"
                  :height="32"
                  fit="contain"
                  :lazy="false"
                  dense
                  class="sku-option-thumb"
                />
                <span class="sku-option-text">{{ val.propertyValue }}</span>
              </button>
            </div>
          </div>

          <div class="qty-line">
            <span class="sku-name">数量</span>
            <div class="qty-control">
              <el-input-number v-model="quantity" :min="1" :max="maxBuy" size="default" />
              <span class="stock-hint">
                库存 {{ selectedSku?.stock ?? '--' }}
                <em v-if="selectedSku?.stock != null && selectedSku.stock <= 5">紧张</em>
              </span>
            </div>
          </div>
        </div>

        <div class="action-panel">
          <div class="action-side">
            <AgentServiceEntry
              :compact="false"
              show-label
              :consult-product="agentConsultProduct"
            />
            <button
              type="button"
              class="btn-fav"
              :class="{ active: favorited }"
              :disabled="favoriteLoading"
              @click="toggleFavorite"
            >
              <el-icon :size="18">
                <StarFilled v-if="favorited" />
                <Star v-else />
              </el-icon>
              <span>{{ favorited ? '已收藏' : '收藏' }}</span>
            </button>
          </div>
          <div class="action-main">
            <el-button class="btn-cart" type="primary" size="large" @click="openAddCartSheet">
              加入购物车
            </el-button>
            <el-button class="btn-buy" type="danger" size="large" @click="buyNow">立即购买</el-button>
          </div>
        </div>
      </div>
    </section>

    <section class="pc-detail-tabs card">
      <el-tabs v-model="detailTab" class="detail-tabs">
        <el-tab-pane label="图文详情" name="desc">
          <MarkdownContent :content="productInfo.productDesc" class="desc-content" center-images />
        </el-tab-pane>
        <el-tab-pane :label="`商品评价${commentTotal > 0 ? ` (${commentTotal})` : ''}`" name="comments">
          <div class="tab-comments">
            <div class="tab-comments-head">
              <p class="comments-summary">
                累计评价 {{ commentTotal }} 条
                <template v-if="commentTotal > 0">
                  · 好评率 {{ commentGoodRate }}%
                  <template v-if="commentImageCount > 0"> · {{ commentImageCount }} 条带图</template>
                </template>
              </p>
              <button v-if="commentTotal > 0" type="button" class="link-more" @click="goAllComments">
                查看全部评价
                <el-icon><ArrowRight /></el-icon>
              </button>
            </div>
            <div v-if="comments.length" class="comment-list">
              <article v-for="c in comments" :key="c.orderId" class="comment-item">
                <div class="comment-head">
                  <div class="user-info">
                    <el-avatar :size="36" :src="resolveAvatarUrl(c.avatar)" class="user-avatar">
                      {{ (c.nickName || '用')[0] }}
                    </el-avatar>
                    <div class="user-detail">
                      <span class="user">{{ maskCommenterName(c.nickName) }}</span>
                      <span v-if="getCommentLevelProxy(c.userId)" class="comment-level-tag" :class="commentLevelTagClass(getCommentLevelProxy(c.userId)!.levelCode)">
                        {{ getCommentLevelProxy(c.userId)!.levelName }}
                      </span>
                    </div>
                  </div>
                  <el-rate v-if="c.star" :model-value="c.star" disabled size="small" />
                </div>
                <p v-if="c.propertyInfo" class="sku-info">{{ c.propertyInfo }}</p>
                <p class="comment-text">{{ c.commentContent }}</p>
                <button
                  type="button"
                  class="report-btn"
                  @click="openReport({ orderId: c.orderId, commentContent: c.commentContent })"
                >
                  举报
                </button>
              </article>
            </div>
            <el-empty v-else description="暂无评价，快来抢沙发吧" :image-size="80" />
          </div>
        </el-tab-pane>
      </el-tabs>
    </section>

    <section class="pc-recommend-block card">
      <h3 class="block-title">看了又看</h3>
      <div v-if="similarProducts.length" class="similar-grid">
        <button
          v-for="item in similarProducts"
          :key="item.productId"
          type="button"
          class="similar-item"
          @click="goDetail(item)"
        >
          <ProductImage :product="item" fit="cover" width="100%" height="100%" class="similar-img" />
          <div class="similar-info">
            <p class="similar-name">{{ item.productName }}</p>
            <p class="similar-price">¥{{ formatPrice(item.price ?? item.salePrice ?? item.minPrice) }}</p>
          </div>
        </button>
      </div>
      <p v-if="loadingMore" class="load-tip">加载中…</p>
      <p v-else-if="finished && similarProducts.length" class="load-tip">已展示全部推荐商品</p>
    </section>
  </div>

  <div v-else-if="loading" class="pc-detail-loading card">
    <el-skeleton animated :rows="12" />
  </div>
  <div v-else class="pc-detail-error card">
    <el-empty :description="loadError ? '商品加载失败' : '商品不存在或已下架'">
      <el-button type="primary" round @click="load">重试</el-button>
      <el-button round @click="router.push('/')">返回首页</el-button>
    </el-empty>
  </div>
  <CommentReportDialog ref="reportDialogRef" />
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ArrowRight, Star, StarFilled } from '@element-plus/icons-vue';
import AgentServiceEntry from '@/components/agent/AgentServiceEntry.vue';
import ProductImage from '@/components/common/ProductImage.vue';
import MarkdownContent from '@/components/common/MarkdownContent.vue';
import CommentReportDialog from '@/components/business/CommentReportDialog.vue';
import { useProductDetailPage } from '@/composables/useProductDetailPage';
import { maskCommenterName } from '@/utils/comment';
import { productApi, userMemberApi } from '@/api/modules';
import { resolveAvatarUrl } from '@/utils/image';

const route = useRoute();
const router = useRouter();

const {
  loading,
  loadError,
  load,
  productInfo,
  productPropertyList,
  comments,
  commentTotal,
  commentGoodRate,
  commentImageCount,
  quantity,
  selectedSku,
  selectedProperty,
  activeImageIndex,
  favorited,
  favoriteLoading,
  detailTab,
  galleryImages,
  displayPrice,
  agentConsultProduct,
  maxBuy,
  openGalleryPreview,
  selectGalleryIndex,
  selectProperty,
  toggleFavorite,
  goAllComments,
  openAddCartSheet,
  buyNow
} = useProductDetailPage();

const reportDialogRef = ref<InstanceType<typeof CommentReportDialog>>();

const similarProducts = ref<any[]>([]);
const loadingMore = ref(false);
const finished = ref(false);
const allSimilarProducts = ref<any[]>([]);
const displayCount = ref(8);
const PAGE_SIZE = 8;
const MAX_SIMILAR = 16;
let scrollTicking = false;

const onScroll = () => {
  if (scrollTicking || finished.value || loadingMore.value) return;
  scrollTicking = true;
  requestAnimationFrame(() => {
    scrollTicking = false;
    const st = window.scrollY || document.documentElement.scrollTop;
    const sh = document.documentElement.scrollHeight;
    const ch = window.innerHeight;
    if (sh - st - ch < 200) loadMore();
  });
};

const loadMore = () => {
  if (finished.value || loadingMore.value) return;
  if (displayCount.value >= allSimilarProducts.value.length) {
    finished.value = true;
    return;
  }
  loadingMore.value = true;
  setTimeout(() => {
    displayCount.value = Math.min(displayCount.value + PAGE_SIZE, MAX_SIMILAR, allSimilarProducts.value.length);
    similarProducts.value = allSimilarProducts.value.slice(0, displayCount.value);
    if (displayCount.value >= allSimilarProducts.value.length) finished.value = true;
    loadingMore.value = false;
  }, 300);
};

const loadSimilarProducts = async () => {
  loadingMore.value = true;
  try {
    const r = await productApi.loadCommendProduct();
    const list = Array.isArray(r) ? r : r?.list || [];
    if (!list.length) { finished.value = true; return; }

    let filled = [...list];
    while (filled.length < MAX_SIMILAR) {
      filled = filled.concat(list);
    }
    allSimilarProducts.value = filled.slice(0, MAX_SIMILAR);
    similarProducts.value = allSimilarProducts.value.slice(0, displayCount.value);
    if (allSimilarProducts.value.length <= displayCount.value) finished.value = true;
  } catch (error) {
    console.error('PcProductDetailView: loadSimilarProducts error', error);
    finished.value = true;
  } finally {
    loadingMore.value = false;
  }
};

const formatPrice = (price: any): string => {
  const n = Number(price);
  if (isNaN(n)) return '--';
  return n.toFixed(2);
};

const goDetail = (p: any) => router.push(`/product/${p.productId}`);

const levelCache = ref<Record<string, { levelCode: number; levelName: string }>>({});

const fetchCommentLevel = (userId: string) => {
  if (!userId || levelCache.value[userId]) return;
  userMemberApi.getLevelBadge(userId).then((res: any) => {
    if (res?.levelCode != null) {
      levelCache.value = { ...levelCache.value, [userId]: res };
    }
  }).catch(() => {});
};

const getCommentLevelProxy = (userId: string | number | undefined): { levelCode: number; levelName: string } | null => {
  if (!userId) return null;
  const key = String(userId);

  fetchCommentLevel(key);
  return levelCache.value[key] ?? null;
};

const commentLevelTagClass = (code: number): string => {
  if (code >= 3) return 'level-gold';
  if (code >= 2) return 'level-silver';
  return 'level-normal';
};

watch(comments, (val) => {
  if (!val?.length) return;
  val.forEach((c: any) => {
    if (c.userId) fetchCommentLevel(String(c.userId));
  });
}, { immediate: true });

onMounted(() => {
  loadSimilarProducts();
  window.addEventListener('scroll', onScroll, { passive: true });
});

onUnmounted(() => window.removeEventListener('scroll', onScroll));

const openReport = (payload: { orderId: string; commentContent?: string }) => {
  reportDialogRef.value?.show({
    orderId: payload.orderId,
    productId: String(route.params.productId || ''),
    commentContent: payload.commentContent
  });
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-product-detail {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.pc-detail-main {
  display: flex;
  flex-wrap: nowrap;
  align-items: flex-start;
  gap: 24px;
  padding: 20px;
  background: $color-card;
  border: 1px solid $color-border-gray;
  border-radius: $radius-card;
  box-shadow: $shadow-card;
}

.pc-detail-gallery {
  flex: 0 0 $pc-detail-gallery-width;
  width: $pc-detail-gallery-width;
  max-width: $pc-detail-gallery-width;
  display: flex;
  gap: 12px;
  flex-shrink: 0;
}

.thumb-strip {
  flex: 0 0 64px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 460px;
  overflow-y: auto;

  .thumb-btn {
    flex-shrink: 0;
    width: 56px;
    height: 56px;
    padding: 0;
    border: 2px solid transparent;
    border-radius: $radius-xs;
    background: #fff;
    cursor: pointer;
    overflow: hidden;

    &.active,
    &:hover {
      border-color: $color-primary;
    }
  }
}

.main-image-wrap {
  flex: 1;
  min-width: 0;
  position: relative;
  aspect-ratio: 1;
  max-height: 460px;
  border: 1px solid $color-border-light;
  border-radius: 4px;
  background: #fff;
  cursor: zoom-in;
  overflow: hidden;

  .main-image,
  .main-image-empty {
    width: 100%;
    height: 100%;
  }

  .image-index {
    position: absolute;
    right: 10px;
    bottom: 10px;
    padding: 2px 8px;
    font-size: 12px;
    color: #fff;
    background: rgba(0, 0, 0, 0.45);
    border-radius: $radius-sm;
  }
}

.pc-detail-info {
  flex: 1 1 $pc-detail-info-min-width;
  min-width: $pc-detail-info-min-width;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.product-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.45;
  color: $color-text-title;
  word-break: normal;
  overflow-wrap: break-word;
}

.price-panel {
  padding: 14px 16px;
  background: $color-bg-subtle;
  border-radius: 4px;

  .price-row {
    display: flex;
    align-items: baseline;
    gap: 12px;
  }

  .price-label {
    font-size: 13px;
    color: $color-text-muted;
  }

  .price-value {
    color: $color-price;
    font-size: 32px;
    font-weight: 700;
    line-height: 1;

    em {
      font-size: 18px;
      font-style: normal;
      margin-right: 2px;
    }
  }

  .sales-meta {
    margin: 8px 0 0;
    font-size: 13px;
    color: $color-text-muted;
  }
}

.price-agent-btn {
  margin-top: 10px;
}

.sku-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sku-line,
.qty-line {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.sku-name {
  flex: 0 0 56px;
  padding-top: 8px;
  font-size: 13px;
  color: $color-text-muted;
}

.sku-options {
  flex: 1;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.sku-option {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  max-width: 260px;
  padding: 6px 12px;
  border: 1px solid $color-border-gray;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  transition: border-color $transition-fast, color $transition-fast;

  &.active {
    border-color: $color-primary;
    color: $color-primary;
    background: $color-gold-soft;
  }

  &:hover:not(.active) {
    border-color: rgba($color-primary, 0.45);
  }

  .sku-option-text {
    font-size: 13px;
    line-height: 1.3;
    text-align: left;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.qty-control {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;

  .stock-hint {
    font-size: 13px;
    color: $color-text-muted;

    em {
      margin-left: 6px;
      font-style: normal;
      color: $color-price;
    }
  }
}

.action-panel {
  margin-top: auto;
  padding-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.action-side {
  display: flex;
  align-items: center;
  gap: 16px;

  .btn-fav {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 6px 10px;
    border: 1px solid $color-border-gray;
    border-radius: $radius-xs;
    background: #fff;
    font-size: 13px;
    color: $color-text-body;
    cursor: pointer;
    transition: border-color $transition-fast, color $transition-fast, background $transition-fast;

    &:hover {
      border-color: rgba($color-primary, 0.45);
      color: $color-primary;
    }

    &.active {
      color: $color-primary;
      border-color: rgba($color-primary, 0.35);
      background: $color-primary-soft;
    }
  }
}

.action-main {
  display: flex;
  flex-wrap: nowrap;
  gap: 12px;

  .btn-cart {
    flex: 0 0 200px;
    width: 200px;
    max-width: 200px;
    height: 48px;
    font-size: 16px;
    font-weight: 600;
    border-radius: $radius-xs;
    --el-button-bg-color: #fff;
    --el-button-text-color: #333;
    --el-button-border-color: #d9d9d9;
    --el-button-hover-bg-color: #fafafa;
    --el-button-hover-text-color: #333;
    --el-button-hover-border-color: #c0c0c0;
    --el-button-active-bg-color: #f0f0f0;
    --el-button-active-border-color: #b3b3b3;
  }

  .btn-buy {
    flex: 0 0 200px;
    width: 200px;
    max-width: 200px;
    height: 48px;
    font-size: 16px;
    font-weight: 600;
    border-radius: $radius-xs;
    --el-button-bg-color: #0F766E;
    --el-button-text-color: #fff;
    --el-button-border-color: #0F766E;
    --el-button-hover-bg-color: #0B665F;
    --el-button-hover-text-color: #fff;
    --el-button-hover-border-color: #0B665F;
    --el-button-active-bg-color: #095A54;
    --el-button-active-border-color: #095A54;
  }
}

.pc-detail-tabs.card {
  padding: 0 16px 16px;
  background: $color-card;
  border: 1px solid $color-border-gray;
  border-radius: $radius-card;
  box-shadow: $shadow-card;

  :deep(.el-tabs__header) {
    margin-bottom: 0;
  }

  :deep(.el-tabs__item) {
    height: 44px;
    font-size: 14px;
  }
}

.tab-comments {
  padding: 16px 4px 8px;
}

.tab-comments-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;

  .comments-summary {
    margin: 0;
    font-size: 14px;
    color: $color-text-body;
  }

  .link-more {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    border: none;
    background: none;
    font-size: 13px;
    color: $color-primary;
    cursor: pointer;
  }
}

.comment-item {
  padding: 14px 0;
  border-bottom: 1px solid $color-border-light;

  &:last-child {
    border-bottom: none;
  }

  .comment-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .user-info {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .user-avatar {
    flex-shrink: 0;
  }

  .user-detail {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .user {
    font-size: 13px;
    color: $color-text-muted;
  }

  .comment-level-tag {
    font-size: 11px;
    padding: 1px 6px;
    border-radius: 2px;

    &.level-gold {
      color: #B8860B;
      background: rgba(184, 134, 11, 0.1);
    }

    &.level-silver {
      color: #757575;
      background: rgba(117, 117, 117, 0.1);
    }

    &.level-normal {
      color: #9E9E9E;
      background: rgba(158, 158, 158, 0.1);
    }
  }

  .sku-info {
    margin: 4px 0 0;
    font-size: 12px;
    color: $color-text-muted;
  }

  .comment-text {
    margin: 8px 0 0;
    font-size: 14px;
    line-height: 1.6;
    color: $color-text-body;
  }
}

.report-btn {
  margin-top: 6px;
  padding: 2px 8px;
  border: none;
  background: transparent;
  font-size: 12px;
  color: $color-text-muted;
  cursor: pointer;
  transition: color $transition-fast;

  &:hover {
    color: $color-error;
  }
}

.desc-content {
  padding: 16px 4px;
  min-height: 120px;
}

.pc-detail-loading,
.pc-detail-error {
  padding: 24px;
  background: $color-card;
  border-radius: $radius-card;
}

.pc-recommend-block {
  padding: 16px;
  background: $color-card;
  border: 1px solid $color-border-gray;
  border-radius: $radius-card;
  box-shadow: $shadow-card;

  .block-title {
    margin: 0 0 12px;
    font-size: 16px;
    font-weight: 600;
    color: $color-text-title;
  }

  .similar-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 16px;
  }

  .similar-item {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 0;
    border: none;
    background: transparent;
    cursor: pointer;
    text-align: left;
    max-width: 220px;

    &:hover .similar-img {
      opacity: 0.9;
    }
  }

  .similar-img {
    width: 100%;
    max-width: 200px;
    aspect-ratio: 1;
    border-radius: $radius-sm;
    background: $color-bg-subtle;
    transition: opacity $transition-fast;
  }

  .similar-info {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .similar-name {
    margin: 0;
    font-size: 13px;
    font-weight: 500;
    color: $color-text-title;
    line-height: 1.35;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .similar-price {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: $color-price;
  }

  .load-tip {
    text-align: center;
    font-size: 13px;
    color: $color-text-muted;
    padding: 12px 0;
    margin: 0;
  }
}
</style>
