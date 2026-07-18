<template>
  <Teleport to="body">
    <Transition name="eshop-lightbox-fade">
      <div
        v-if="imagePreviewState.visible"
        class="eshop-image-lightbox allow-pinch-zoom"
        role="dialog"
        aria-modal="true"
        @click="closeImagePreview"
        @wheel.prevent="onWheel"
        @mouseup="onMouseUp"
        @mouseleave="onMouseUp"
      >
        <img
          :key="currentUrl"
          :src="currentUrl"
          class="eshop-image-lightbox__img allow-pinch-zoom"
          :style="imgStyle"
          alt=""
          draggable="false"
          @click.stop
          @touchstart="onTouchStart"
          @touchmove="onTouchMove"
          @touchend="onTouchEnd"
          @touchcancel="onTouchEnd"
          @mousedown="onMouseDown"
          @mousemove="onMouseMove"
        />
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue';
import { closeImagePreview, imagePreviewState } from '@/composables/imagePreview';
import { useImageLightboxZoom } from '@/composables/useImageLightboxZoom';

const currentUrl = computed(() => imagePreviewState.urls[imagePreviewState.index] ?? '');

const {
  imgStyle,
  resetTransform,
  onWheel,
  onTouchStart,
  onTouchMove,
  onTouchEnd,
  onMouseDown,
  onMouseMove,
  onMouseUp
} = useImageLightboxZoom();

const prev = () => {
  const n = imagePreviewState.urls.length;
  if (n <= 1) return;
  imagePreviewState.index = (imagePreviewState.index - 1 + n) % n;
};

const next = () => {
  const n = imagePreviewState.urls.length;
  if (n <= 1) return;
  imagePreviewState.index = (imagePreviewState.index + 1) % n;
};

const onKeydown = (e: KeyboardEvent) => {
  if (!imagePreviewState.visible) return;
  if (e.key === 'Escape') closeImagePreview();
  else if (e.key === 'ArrowLeft') prev();
  else if (e.key === 'ArrowRight') next();
};

watch(currentUrl, () => resetTransform());

watch(
  () => imagePreviewState.visible,
  (open) => {
    document.body.style.overflow = open ? 'hidden' : '';
    if (!open) resetTransform();
  }
);

onMounted(() => window.addEventListener('keydown', onKeydown));
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown);
  document.body.style.overflow = '';
  resetTransform();
});
</script>

<style lang="scss">
.eshop-image-lightbox {
  position: fixed;
  inset: 0;
  z-index: 4000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  box-sizing: border-box;
  background: rgba(0, 0, 0, 0.88);
  cursor: zoom-out;

  &__img {
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
    user-select: none;
    -webkit-user-drag: none;
    transform-origin: center center;
    will-change: transform;
    touch-action: none;
    cursor: grab;

    &:active {
      cursor: grabbing;
    }
  }
}

.eshop-lightbox-fade-enter-active,
.eshop-lightbox-fade-leave-active {
  transition: opacity 0.2s ease;
}

.eshop-lightbox-fade-enter-from,
.eshop-lightbox-fade-leave-to {
  opacity: 0;
}
</style>
