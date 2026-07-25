<template>
  <el-dialog
    v-model="visible"
    title="评价详情"
    width="92%"
    :style="{ maxWidth: '520px' }"
    destroy-on-close
    class="comment-preview-dialog"
  >
    <el-skeleton v-if="loading" animated :rows="6" />
    <template v-else>
      <div v-if="data" class="preview">
        <header
          v-if="data.productName || productCover"
          class="product-head"
          role="button"
          tabindex="0"
          @click="goProductDetail"
          @keydown.enter="goProductDetail"
        >
          <img v-if="productCover" :src="productCover" class="product-cover" alt="" />
          <div class="product-meta">
            <p class="product-name">{{ data.productName || '商品' }}</p>
            <p v-if="data.propertyInfo" class="product-spec">{{ data.propertyInfo }}</p>
          </div>
          <el-icon class="product-arrow"><ArrowRight /></el-icon>
        </header>

        <div class="row">
          <span class="label">评价星级</span>
          <el-rate :model-value="Number(data.star || 0)" disabled />
        </div>
        <div v-if="data.commentTime" class="row">
          <span class="label">评价时间</span>
          <span class="value">{{ formatTime(data.commentTime) }}</span>
        </div>
        <div class="row col">
          <span class="label">评价内容</span>
          <p class="text">{{ data.commentContent || '（无）' }}</p>
        </div>
        <div v-if="commentImages.length" class="row col">
          <span class="label">评价图片</span>
          <div class="img-list">
            <img
              v-for="(img, idx) in commentImages"
              :key="idx"
              :src="toImageSrc(img)"
              class="img"
              alt=""
              @click="previewImage(commentImages, idx)"
            />
          </div>
        </div>

        <div v-if="data.commentBizReply" class="biz-reply">
          <p class="biz-label">商家回复</p>
          <p class="text">{{ data.commentBizReply }}</p>
        </div>

        <div v-if="data.recommentContent || recommentImages.length" class="divider">追评</div>
        <div v-if="data.recommentTime" class="row">
          <span class="label">追评时间</span>
          <span class="value">{{ formatTime(data.recommentTime) }}</span>
        </div>
        <div v-if="data.recommentContent" class="row col">
          <span class="label">追评内容</span>
          <p class="text">{{ data.recommentContent }}</p>
        </div>
        <div v-if="recommentImages.length" class="row col">
          <span class="label">追评图片</span>
          <div class="img-list">
            <img
              v-for="(img, idx) in recommentImages"
              :key="idx"
              :src="toImageSrc(img)"
              class="img"
              alt=""
              @click="previewImage(recommentImages, idx)"
            />
          </div>
        </div>
      </div>
      <el-empty v-else description="暂无评价内容" />
    </template>
    <template #footer>
      <el-button type="primary" @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { ArrowRight } from '@element-plus/icons-vue';
import { commentApi, productApi } from '@/api/modules';
import { isProductOnSale } from '@/utils/product';
import { resolveImageUrl, splitImagePaths } from '@/utils/image';
import { openImagePreview, closeImagePreview } from '@/composables/imagePreview';
import { toast } from '@/utils/toast';

const router = useRouter();
const visible = ref(false);
const loading = ref(false);
const navigating = ref(false);
const data = ref<Record<string, any> | null>(null);

const toImageSrc = (path: string) => resolveImageUrl(path, { useThumbnail: false }) || path;

const commentImages = computed(() => splitImagePaths(data.value?.commentImages as string | null));
const recommentImages = computed(() => splitImagePaths(data.value?.recommentImages as string | null));

const productCover = computed(() => {
  const cover = data.value?.cover;
  return cover ? resolveImageUrl(cover) : '';
});

const formatTime = (val: unknown) => {
  if (!val) return '--';
  if (typeof val === 'string') return val.replace('T', ' ').slice(0, 19);
  const d = new Date(val as string | number);
  if (Number.isNaN(d.getTime())) return String(val);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};

const previewImage = (list: string[], index: number) => {
  const urls = list.map((p) => toImageSrc(p)).filter(Boolean);
  if (!urls.length) return;
  openImagePreview(urls, Math.min(Math.max(index, 0), urls.length - 1));
};

const goProductDetail = async () => {
  const productId = data.value?.productId;
  if (!productId) {
    toast.warning('无法获取商品信息');
    return;
  }
  if (navigating.value) return;
  navigating.value = true;
  try {
    const res = await productApi.getProduct(String(productId));
    const info = res?.productInfo;
    if (!info) {
      toast.warning('商品不存在或已下架');
      return;
    }
    if (!isProductOnSale(info)) {
      toast.warning('该商品已下架');
      return;
    }
    visible.value = false;
    await router.push(`/product/${productId}`);
  } catch {
    toast.warning('商品不存在或已下架');
  } finally {
    navigating.value = false;
  }
};

const show = async (payload: string | Record<string, unknown>) => {
  visible.value = true;
  loading.value = true;
  data.value = null;
  try {
    if (typeof payload === 'object' && payload !== null) {
      data.value = { ...payload };
      return;
    }
    data.value = (await commentApi.getComment(payload)) || null;
  } finally {
    loading.value = false;
  }
};

watch(visible, (open) => {
  if (!open) closeImagePreview();
});

defineExpose({ show });
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.preview {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: min(70vh, 560px);
  overflow-y: auto;
}

.product-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 12px;
  border-bottom: 1px solid $color-border-light;
  cursor: pointer;
  border-radius: $radius-xs;
  transition: background 0.15s ease;

  &:hover {
    background: $color-bg-subtle;
  }

  &:active {
    opacity: 0.92;
  }
}

.product-arrow {
  flex-shrink: 0;
  font-size: 16px;
  color: $color-text-muted;
}

.product-cover {
  width: 56px;
  height: 56px;
  border-radius: $radius-xs;
  object-fit: cover;
  flex-shrink: 0;
  background: $color-bg-subtle;
}

.product-meta {
  flex: 1;
  min-width: 0;
}

.product-name {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: $color-text-title;
  line-height: 1.4;
}

.product-spec {
  margin: 4px 0 0;
  font-size: 12px;
  color: $color-text-muted;
}

.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.row.col {
  align-items: flex-start;
  flex-direction: column;
}

.label {
  font-size: 12px;
  color: $color-text-muted;
  flex-shrink: 0;
}

.value {
  font-size: 13px;
  color: $color-text-body;
}

.text {
  margin: 0;
  font-size: 14px;
  line-height: 1.55;
  color: $color-text-body;
  white-space: pre-wrap;
  word-break: break-word;
}

.img-list {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  width: 100%;
}

.img {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  border-radius: $radius-sm;
  background: $color-bg-subtle;
  cursor: pointer;
}

.biz-reply {
  padding: 10px 12px;
  border-radius: $radius-btn;
  background: rgba($color-primary, 0.06);
  border: 1px solid rgba($color-primary, 0.12);

  .biz-label {
    margin: 0 0 6px;
    font-size: 12px;
    font-weight: 600;
    color: $color-primary;
  }
}

.divider {
  margin-top: 2px;
  padding-top: 10px;
  border-top: 1px solid $color-border;
  font-size: 13px;
  font-weight: 600;
  color: $color-text-title;
}
</style>
