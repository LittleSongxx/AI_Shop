<template>
  <div v-if="productInfo" class="detail-simlect">

    <section class="gallery-wrap">
      <div
        v-if="galleryImages.length"
        class="gallery-swipe"
        @touchstart="onTouchStart"
        @touchmove="onTouchMove"
        @touchend="onTouchEnd"
        @mousedown="onMouseDown"
        @mousemove="onMouseMove"
        @mouseup="onMouseUp"
        @mouseleave="onMouseUp"
      >
        <div class="gallery-track" :style="{ transform: `translateX(-${activeImageIndex * 100}%)` }" @dragstart.prevent>
          <div v-for="(img, i) in galleryImages" :key="i" class="gallery-slide">
            <ProductImage :source="img" :use-thumbnail="false" width="100%" height="100%" class="gallery-img" />
          </div>
        </div>
      </div>
      <div v-else class="gallery-empty">
        <ProductImage width="100%" height="100%" />
      </div>
      <div v-if="galleryImages.length > 1" class="gallery-indicator">
        {{ activeImageIndex + 1 }} / {{ galleryImages.length }}
      </div>
    </section>

    <section class="block info-block">
      <h1 class="product-name">{{ productInfo.productName }}</h1>
      <div class="price-line">
        <span class="currency">¥</span>
        <span class="amount">{{ displayPrice }}</span>
      </div>
      <p class="sales-line">销量 {{ productInfo.totalSale ?? 0 }}</p>
      <AgentServiceEntry
        class="info-agent-btn"
        :compact="false"
        show-label
        :icon-size="18"
        :consult-product="agentConsultProduct"
      />
    </section>

    <section class="block quality-badges-row">
      <span class="quality-badge gold">
        <BrandMark class="badge-icon" />智选自营
      </span>
      <span class="quality-badge">
        <i class="badge-dot"></i>正品保证
      </span>
      <span class="quality-badge">
        <i class="badge-dot"></i>品质溯源
      </span>
      <span class="quality-badge">
        <i class="badge-dot"></i>售后无忧
      </span>
    </section>

    <section class="block sku-block-wrap">
      <div v-for="prop in productPropertyList" :key="prop.propertyId" class="sku-row">
        <div class="sku-label">{{ prop.propertyName }}</div>
        <div class="sku-values">
          <button
            v-for="val in prop.propertyValues"
            :key="val.propertyValueId"
            type="button"
            class="sku-tag"
            :class="{ active: selectedProperty[prop.propertyId] === val.propertyValueId }"
            @click="selectProperty(prop, val)"
          >
            <ProductImage
              v-if="val.propertyCover"
              :source="val.propertyCover"
              :width="18"
              :height="18"
              fit="contain"
              :lazy="false"
              dense
              class="sku-thumb ignore"
            />
            <span>{{ val.propertyValue }}</span>
          </button>
        </div>
      </div>
      <div class="qty-row">
        <span class="sku-label">数量</span>
        <span class="stock-tip">
          库存 {{ selectedSku?.stock ?? '--' }}
          <em v-if="selectedSku?.stock != null && selectedSku.stock <= 5">紧张</em>
        </span>
        <el-input-number v-model="quantity" :min="1" :max="maxBuy" size="small" />
      </div>
    </section>

    <section class="block delivery-block">
      <div class="info-row">
        <span class="info-label">配送</span>
        <span class="info-value">预计3-5个工作日送达 · 包邮</span>
      </div>
    </section>

    <section class="block service-block">
      <div class="info-row">
        <span class="info-label">服务</span>
        <div class="service-badges">
          <span class="badge"><i class="badge-icon">✓</i>7天无理由退货</span>
          <span class="badge"><i class="badge-icon">✓</i>正品保障</span>
          <span class="badge"><i class="badge-icon">✓</i>极速退款</span>
        </div>
      </div>
    </section>

    <section class="block store-block">
      <div class="store-row">
        <BrandMark class="store-logo" />
        <div class="store-info">
          <div class="store-name">
            {{ productInfo.storeName || '智选自营' }}
            <span v-if="!productInfo.storeName" class="store-badge">自营</span>
          </div>
        </div>
      </div>
    </section>

    <section class="block comment-block">
      <div class="block-head">
        <div>
          <h3>商品评价</h3>
          <p v-if="commentTotal > 0" class="comment-stats">
            好评率 {{ commentGoodRate }}%
            <span v-if="commentImageCount > 0"> · {{ commentImageCount }} 条带图</span>
          </p>
        </div>
        <button type="button" class="link-all" @click="goAllComments">
          查看全部{{ commentTotal > 0 ? `(${commentTotal})` : '' }}
          <el-icon><ArrowRight /></el-icon>
        </button>
      </div>
      <div v-if="previewComments.length" class="comment-preview-list">
        <article v-for="c in previewComments" :key="c.orderId" class="comment-preview-item">
          <div class="preview-head">
            <span class="user">{{ maskCommenterName(c.nickName) }}</span>
            <span v-if="getCommentLevel(c.userId)" class="comment-level-tag" :class="commentLevelTagClass(getCommentLevel(c.userId)!.levelCode)">
              {{ getCommentLevel(c.userId)!.levelName }}
            </span>
            <el-rate v-if="c.star" :model-value="c.star" disabled size="small" />
          </div>
          <p class="text">{{ c.commentContent }}</p>
          <button
            type="button"
            class="report-btn"
            @click="openReport({ orderId: c.orderId, commentContent: c.commentContent })"
          >
            举报
          </button>
        </article>
      </div>
      <p v-else class="empty-tip">暂无评价，快来抢沙发吧</p>
    </section>

    <section class="block desc-block">
      <h3 class="block-title">图文详情</h3>
      <MarkdownContent :content="productInfo.productDesc" class="desc-body" />
    </section>

    <section class="block similar-block">
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
          <p class="similar-name">{{ item.productName }}</p>
          <p class="similar-price">¥{{ formatPrice(item.price ?? item.salePrice ?? item.minPrice) }}</p>
        </button>
      </div>
      <p v-if="loadingMore" class="load-tip">加载中…</p>
      <p v-else-if="finished && similarProducts.length" class="load-tip">已展示全部推荐商品</p>
      <p v-else-if="!similarProducts.length && !loadingMore" class="similar-empty">暂无推荐</p>
    </section>

    <LiquidGlassSurface tag="footer" intensity="medium" class="detail-footer ignore">
      <AgentServiceEntry
        class="footer-agent"
        :compact="false"
        show-label
        :consult-product="agentConsultProduct"
      />
      <button
        type="button"
        class="footer-fav"
        :class="{ active: favorited }"
        :disabled="favoriteLoading"
        aria-label="收藏"
        @click="toggleFavorite"
      >
        <el-icon :size="22">
          <StarFilled v-if="favorited" />
          <Star v-else />
        </el-icon>
        <span class="label">{{ favorited ? '已收藏' : '收藏' }}</span>
      </button>
      <el-button class="btn-cart" type="primary" plain round @click="openAddCartSheet">加入购物车</el-button>
      <el-button class="btn-buy" type="primary" round @click="buyNow">立即购买</el-button>
    </LiquidGlassSurface>
    <CommentReportDialog ref="reportDialogRef" />
  </div>
  <div v-else-if="loading" class="detail-loading card">
    <el-skeleton animated :rows="10" />
  </div>
  <div v-else class="detail-error card">
    <el-empty :description="loadError ? '商品加载失败' : '商品不存在或已下架'">
      <el-button type="primary" round @click="load">重试</el-button>
      <el-button round @click="router.push('/')">返回首页</el-button>
    </el-empty>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ArrowRight, Star, StarFilled } from '@element-plus/icons-vue';
import AgentServiceEntry from '@/components/agent/AgentServiceEntry.vue';
import LiquidGlassSurface from '@/components/common/LiquidGlassSurface.vue';
import ProductImage from '@/components/common/ProductImage.vue';
import MarkdownContent from '@/components/common/MarkdownContent.vue';
import BrandMark from '@/components/common/BrandMark.vue';
import CommentReportDialog from '@/components/business/CommentReportDialog.vue';
import { useProductDetailPage } from '@/composables/useProductDetailPage';
import { maskCommenterName } from '@/utils/comment';
import { userMemberApi, productApi } from '@/api/modules';

const route = useRoute();
const router = useRouter();

const {
  loading,
  loadError,
  load,
  productInfo,
  productPropertyList,
  quantity,
  selectedSku,
  selectedProperty,
  activeImageIndex,
  favorited,
  favoriteLoading,
  galleryImages,
  displayPrice,
  agentConsultProduct,
  previewComments,
  commentTotal,
  commentGoodRate,
  commentImageCount,
  maxBuy,
  onTouchStart,
  onTouchMove,
  onTouchEnd,
  onMouseDown,
  onMouseMove,
  onMouseUp,
  selectProperty,
  toggleFavorite,
  goAllComments,
  openAddCartSheet,
  buyNow
} = useProductDetailPage();

const commentLevelCache = ref<Record<string, { levelCode: number; levelName: string }>>({});

const fetchCommentLevel = (userId: string) => {
  if (!userId || commentLevelCache.value[userId]) return;
  userMemberApi.getLevelBadge(userId).then((res: any) => {
    if (res?.levelCode != null) {
      commentLevelCache.value = { ...commentLevelCache.value, [userId]: res };
    }
  }).catch(() => {});
};

const getCommentLevel = (userId?: string) => (userId && commentLevelCache.value[userId]) || null;

const commentLevelTagClass = (code: number) => {
  if (code >= 3) return 'level-gold';
  if (code >= 2) return 'level-silver';
  return 'level-default';
};

watch(previewComments, () => {
  previewComments.value.forEach((c: any) => {
    if (c.userId) fetchCommentLevel(c.userId);
  });
}, { immediate: true });

const reportDialogRef = ref<InstanceType<typeof CommentReportDialog>>();

const similarProducts = ref<any[]>([]);
const loadingMore = ref(false);
const finished = ref(false);
const allSimilarProducts = ref<any[]>([]);
const displayCount = ref(6);
const PAGE_SIZE = 6;
const maxSimilarProducts = 12;
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
    displayCount.value = Math.min(displayCount.value + PAGE_SIZE, maxSimilarProducts, allSimilarProducts.value.length);
    similarProducts.value = allSimilarProducts.value.slice(0, displayCount.value);
    if (displayCount.value >= allSimilarProducts.value.length) finished.value = true;
    loadingMore.value = false;
  }, 300);
};

const loadSimilarProducts = async () => {
  loadingMore.value = true;
  try {
    const data = await productApi.loadCommendProduct();
    const list = Array.isArray(data) ? data : data?.list || [];
    if (!list.length) { finished.value = true; return; }

    let filled = [...list];
    while (filled.length < maxSimilarProducts) {
      filled = filled.concat(list);
    }
    allSimilarProducts.value = filled.slice(0, maxSimilarProducts);
    similarProducts.value = allSimilarProducts.value.slice(0, displayCount.value);
    if (allSimilarProducts.value.length <= displayCount.value) finished.value = true;
  } catch (error) {
    console.error('ProductDetailView: loadSimilarProducts error', error);
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

const goDetail = (p: any) => {
  if (p?.productId) router.push(`/product/${p.productId}`);
};

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

.detail-simlect {
  margin: 0 -16px;
  padding-bottom: calc(56px + env(safe-area-inset-bottom, 0));
  background: transparent;
}

.detail-loading {
  margin: 12px;
  padding: 16px;
}

.gallery-wrap {
  position: relative;
  width: 100%;
  max-height: 420px;
  background: #fff;
  overflow: hidden;
}

.gallery-swipe {
  overflow: hidden;
  touch-action: pan-x pan-y;
  user-select: none;
  cursor: grab;

  &:active {
    cursor: grabbing;
  }
}

.gallery-track {
  display: flex;
  transition: transform 0.3s ease;
}

.gallery-slide {
  flex: 0 0 100%;
  width: 100%;
  aspect-ratio: 1;
  max-height: 420px;
}

.gallery-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #fff;
}

.gallery-empty {
  height: 280px;
  background: $color-bg;
}

.gallery-indicator {
  position: absolute;
  right: 12px;
  bottom: 12px;
  padding: 2px 10px;
  font-size: 12px;
  color: #fff;
  background: rgba(0, 0, 0, 0.45);
  border-radius: 8px;
  z-index: 2;
}

.block {
  margin-top: 8px;
  padding: 14px 16px;
}

.info-block {
  .product-name {
    margin: 0 0 10px;
    font-size: 16px;
    font-weight: 600;
    line-height: 1.45;
    color: $color-text-title;
  }

  .price-line {
    display: flex;
    align-items: baseline;
    color: $color-price;
    line-height: 1;

    .currency {
      font-size: 16px;
      font-weight: 700;
    }

    .amount {
      font-size: 28px;
      font-weight: 700;
    }
  }

  .sales-line {
    margin: 8px 0 0;
    font-size: 12px;
    color: $color-text-muted;
  }
}

.info-agent-btn {
  margin-top: 12px;
}

.sku-block-wrap {
  .sku-row {
    margin-bottom: 12px;
  }

  .sku-label {
    font-size: 13px;
    color: $color-text-muted;
    margin-bottom: 8px;
  }

  .sku-values {
    display: flex;
    flex-wrap: nowrap;
    gap: 8px;
    overflow-x: auto;
    padding-bottom: 2px;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;

    &::-webkit-scrollbar {
      display: none;
    }
  }

  .sku-tag {
    display: inline-flex;
    flex-shrink: 0;
    align-items: center;
    gap: 4px;
    padding: 6px 12px;
    border: 1px solid $color-border;
    border-radius: $radius-pill;
    background: #fafafa;
    font-size: 12px;
    white-space: nowrap;
    cursor: pointer;
    transition: border-color $transition-fast, color $transition-fast, background $transition-fast;

    &.active {
      border-color: $color-primary;
      color: $color-primary;
      background: $color-primary-muted;
      font-weight: 600;
    }

    .sku-thumb {
      flex-shrink: 0;
      width: 18px;
      height: 18px;
      border-radius: $radius-xs;
      overflow: hidden;
      background: #fff;
      border: 1px solid rgba($color-border, 0.55);

      :deep(.product-image) {
        width: 18px !important;
        height: 18px !important;
        border-radius: $radius-xs;
      }
    }
  }

  .qty-row {
    display: flex;
    flex-wrap: nowrap;
    align-items: center;
    gap: 10px;
    padding-top: 4px;
    overflow-x: auto;
    scrollbar-width: none;

    &::-webkit-scrollbar {
      display: none;
    }

    .stock-tip {
      flex: 1;
      font-size: 12px;
      color: $color-text-muted;

      em {
        margin-left: 6px;
        font-style: normal;
        color: $color-price;
      }
    }
  }
}

.delivery-block {
  .info-row {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .info-label {
    flex-shrink: 0;
    font-size: 13px;
    color: $color-text-muted;
  }

  .info-value {
    font-size: 13px;
    color: $color-text-body;
  }
}

.quality-badges-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 0;
}

.quality-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 12px;
    border-radius: $radius-tag;
    background: $color-bg-subtle;
    color: $color-text-body;
    font-size: 12px;
    font-weight: 500;
    white-space: nowrap;

    .badge-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: $color-text-muted;
      flex-shrink: 0;
    }

    .badge-icon {
      width: 14px;
      height: 14px;
      flex-shrink: 0;
    }

    &.gold {
      background: $color-gold-soft;
      color: #8b7355;
      font-weight: 600;

      .badge-dot {
        background: $color-gold;
      }

      .badge-icon {
        :deep(.brand-mark) {
          color: $color-gold;
        }
      }
    }
  }

.service-block {
  .info-row {
    display: flex;
    align-items: flex-start;
    gap: 12px;
  }

  .info-label {
    flex-shrink: 0;
    font-size: 13px;
    color: $color-text-muted;
    line-height: 22px;
  }

  .service-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 16px;
  }

  .badge {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 13px;
    color: $color-text-body;
    white-space: nowrap;
  }

  .badge-icon {
    font-style: normal;
    font-size: 12px;
    color: $color-primary;
    font-weight: 700;
  }
}

.store-block {
  .store-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .store-logo {
    flex-shrink: 0;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: $color-primary-muted;
    display: flex;
    align-items: center;
    justify-content: center;

    :deep(.brand-mark) {
      width: 22px;
      height: 22px;
      color: $color-primary;
    }
  }

  .store-info {
    flex: 1;
    min-width: 0;
  }

  .store-name {
    font-size: 14px;
    font-weight: 600;
    color: $color-text-title;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .store-badge {
    display: inline-block;
    padding: 1px 5px;
    font-size: 10px;
    font-weight: 600;
    color: #fff;
    background: $color-primary;
    border-radius: $radius-xs;
    line-height: 1.4;
  }

  .store-enter-btn {
    flex-shrink: 0;
    padding: 4px 12px;
    border: 1px solid $color-border;
    border-radius: $radius-pill;
    background: transparent;
    font-size: 12px;
    color: $color-text-body;
    cursor: pointer;
    transition: border-color $transition-fast, color $transition-fast;

    &:hover {
      border-color: $color-primary;
      color: $color-primary;
    }
  }

}

.similar-block {
  .block-title {
    margin: 0 0 10px;
    font-size: 15px;
    font-weight: 600;
    color: $color-text-title;
  }

  .similar-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    max-width: 400px;
    margin: 0 auto;
  }

  .similar-item {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 0;
    border: none;
    background: transparent;
    cursor: pointer;
    text-align: left;
    max-width: 200px;

    &:active {
      opacity: 0.8;
    }
  }

  .similar-img {
    width: 100%;
    max-width: 180px;
    aspect-ratio: 1;
    border-radius: $radius-sm;
    background: $color-bg-subtle;
    margin: 0 auto;
  }

  .similar-name {
    margin: 0;
    font-size: 12px;
    font-weight: 500;
    color: $color-text-title;
    line-height: 1.35;
    display: -webkit-box;
    -webkit-line-clamp: 1;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .similar-price {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: $color-price;
  }

  .similar-empty {
    margin: 0;
    padding: 20px 0;
    text-align: center;
    font-size: 13px;
    color: $color-text-muted;
  }

  .load-tip {
    text-align: center;
    font-size: 12px;
    color: $color-text-muted;
    padding: 8px 0;
    margin: 0;
  }
}

.comment-block {
  .block-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;

    h3 {
      margin: 0;
      font-size: 15px;
      font-weight: 600;
    }

    .link-all {
      display: inline-flex;
      align-items: center;
      gap: 2px;
      border: none;
      background: none;
      font-size: 13px;
      color: $color-text-muted;
      cursor: pointer;

      &:hover {
        color: $color-primary;
      }
    }
  }
}

.comment-preview-item {
  padding: 10px 0;
  border-bottom: 1px solid $color-border;

  &:last-child {
    border-bottom: none;
  }

  .preview-head {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;

    .el-rate {
      margin-left: auto;
      flex-shrink: 0;
    }
  }

  .user {
    font-size: 12px;
    color: $color-text-muted;
  }

  .comment-level-tag {
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

  .text {
    margin: 6px 0 0;
    font-size: 14px;
    line-height: 1.5;
    color: $color-text-body;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
}

.report-btn {
  margin-top: 6px;
  padding: 2px 8px;
  border: none;
  background: transparent;
  font-size: 11px;
  color: $color-text-muted;
  cursor: pointer;
  transition: color $transition-fast;

  &:hover {
    color: $color-error;
  }
}

.empty-tip {
  margin: 0;
  font-size: 13px;
  color: $color-text-muted;
}

.desc-block {
  position: relative;
  z-index: 1;

  .block-title {
    margin: 0 0 10px;
    font-size: 15px;
    font-weight: 600;
    color: $color-text-title !important;
  }

  .desc-body {
    width: 100%;
    position: relative;
    z-index: 1;

    :deep(p),
    :deep(span),
    :deep(h1),
    :deep(h2),
    :deep(h3),
    :deep(h4),
    :deep(h5),
    :deep(h6) {
      color: $color-text-body !important;
    }
  }
}

.detail-footer {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1002;
  border-top: 1px solid var(--glass-border-soft);
  box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.08);

  :deep(.liquid-glass-surface__content) {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 12px;
    padding-bottom: calc(8px + env(safe-area-inset-bottom, 0));
    width: 100%;
  }

  .footer-agent {
    flex-shrink: 0;
  }

  .footer-fav {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    padding: 0 4px;
    border: none;
    background: none;
    font-size: 10px;
    color: $color-text-muted;
    cursor: pointer;

    &.active {
      color: $color-primary;
    }

    &:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }
  }

  .btn-cart,
  .btn-buy {
    flex: 1;
    min-width: 0;
    height: 40px;
    font-size: 15px;
    font-weight: 600;
  }
}
</style>
