<template>
  <el-image
    :src="src"
    :fit="fit"
    :lazy="lazy"
    class="product-image"
    :class="{ 'is-dense': dense }"
    :style="sizeStyle"
  >
    <template #placeholder>
      <div class="img-placeholder">
        <el-icon :size="iconSize"><Picture /></el-icon>
      </div>
    </template>
    <template #error>
      <div class="img-placeholder">
        <el-icon :size="iconSize"><Picture /></el-icon>
      </div>
    </template>
  </el-image>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { Picture } from '@element-plus/icons-vue';
import { pickProductCover, resolveImageUrl } from '@/utils/image';

const props = withDefaults(
  defineProps<{
    source?: string | null;
    product?: Record<string, any>;
    width?: number | string;
    height?: number | string;
    fit?: 'cover' | 'contain' | 'fill';
    lazy?: boolean;
    useThumbnail?: boolean;

    dense?: boolean;
  }>(),
  { fit: 'cover', lazy: true, useThumbnail: true, dense: false }
);

const src = computed(() => {
  const raw = props.source ?? (props.product ? pickProductCover(props.product) : '');
  return resolveImageUrl(raw, { useThumbnail: props.useThumbnail });
});

const toCssSize = (val?: number | string) => {
  if (val == null || val === '') return undefined;
  if (typeof val === 'number' && Number.isFinite(val)) return `${val}px`;
  const text = String(val).trim();
  if (/^\d+(\.\d+)?$/.test(text)) return `${text}px`;
  return text;
};

const sizeStyle = computed(() => ({
  width: toCssSize(props.width) ?? '100%',
  height: toCssSize(props.height) ?? '100%'
}));

const iconSize = computed(() => {
  const w = Number(props.width);
  if (!Number.isNaN(w) && w > 0) return Math.min(32, Math.max(20, Math.floor(w / 3)));
  return 28;
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.product-image {
  display: block;
  border-radius: $radius-sm;
  overflow: hidden;
  background: transparent;

  :deep(.el-image) {
    width: 100%;
    height: 100%;
    display: block;
  }

  :deep(.el-image__inner) {
    width: 100%;
    height: 100%;
    max-width: 100%;
    max-height: 100%;
  }
}

.img-placeholder {
  width: 100%;
  height: 100%;
  min-height: 60px;
  display: grid;
  place-items: center;
  color: #ccc;
  background: linear-gradient(135deg, #ffffff, #f8f8f8);
}

.product-image.is-dense .img-placeholder {
  min-height: 0;
}
</style>
