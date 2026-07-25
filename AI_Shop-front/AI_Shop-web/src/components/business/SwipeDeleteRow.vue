<template>
  <div ref="rootRef" class="swipe-delete-row" :style="rowStyle">
    <div
      class="swipe-track"
      :class="{ dragging: isDragging }"
      :style="{ transform: `translate3d(${offsetX}px, 0, 0)` }"
      @touchstart="onTouchStart"
      @touchmove="onTouchMove"
      @touchend="onTouchEnd"
      @touchcancel="onTouchEnd"
      @mousedown="onMouseDown"
    >
      <div class="swipe-content">
        <slot />
      </div>
      <div
        v-if="deletable"
        class="swipe-action"
        :style="{
          width: `${effectiveActionWidth}px`,
          opacity: actionOpacity,
          transform: `scale(${actionScale})`,
        }"
      >
        <button
          type="button"
          class="swipe-del-btn"
          :style="{ pointerEvents: actionClickable ? 'auto' : 'none' }"
          @click.stop="onDelete"
        >
          删除
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

const props = withDefaults(
  defineProps<{
    open?: boolean;
    actionWidth?: number;

    deletable?: boolean;
  }>(),
  { open: false, actionWidth: 72, deletable: true }
);

const effectiveActionWidth = computed(() => (props.deletable ? props.actionWidth : 0));

const emit = defineEmits<{
  open: [];
  close: [];
  delete: [];
}>();

const rootRef = ref<HTMLElement | null>(null);
const rowWidth = ref(0);
const offsetX = ref(0);
const isDragging = ref(false);

let startX = 0;
let startOffset = 0;
let tracking = false;
let resizeObserver: ResizeObserver | null = null;

const swipeProgress = computed(() => {
  const w = effectiveActionWidth.value;
  if (!w) return 0;
  return Math.min(1, Math.abs(offsetX.value) / w);
});

const actionOpacity = computed(() => {
  const p = swipeProgress.value;
  if (p <= 0) return 0;
  return 0.35 + 0.65 * p;
});

const actionScale = computed(() => 0.88 + 0.12 * swipeProgress.value);

const actionClickable = computed(() => swipeProgress.value > 0.45);

const rowStyle = computed(() => ({
  '--swipe-row-w': rowWidth.value ? `${rowWidth.value}px` : '100%',
}));

const clampOffset = (x: number) => {
  const w = effectiveActionWidth.value;
  if (!w) return 0;
  return Math.min(0, Math.max(-w, x));
};

const snap = () => {
  const w = effectiveActionWidth.value;
  if (!w) {
    offsetX.value = 0;
    emit('close');
    return;
  }
  if (offsetX.value <= -w / 2) {
    offsetX.value = -w;
    emit('open');
  } else {
    offsetX.value = 0;
    emit('close');
  }
};

const measureRow = () => {
  rowWidth.value = rootRef.value?.clientWidth ?? 0;
};

watch(
  () => props.open,
  (open) => {
    if (!isDragging.value) {
      const w = effectiveActionWidth.value;
      offsetX.value = open && w ? -w : 0;
    }
  },
  { immediate: true }
);

const onTouchStart = (e: TouchEvent) => {
  if (!props.deletable || e.touches.length !== 1) return;
  isDragging.value = true;
  tracking = true;
  startX = e.touches[0].clientX;
  startOffset = offsetX.value;
};

const onTouchMove = (e: TouchEvent) => {
  if (!tracking || e.touches.length !== 1) return;
  const dx = e.touches[0].clientX - startX;
  offsetX.value = clampOffset(startOffset + dx);
  if (Math.abs(dx) > 6) e.preventDefault();
};

const onTouchEnd = () => {
  if (!tracking) return;
  tracking = false;
  isDragging.value = false;
  snap();
};

const onMouseMove = (e: MouseEvent) => {
  if (!tracking) return;
  const dx = e.clientX - startX;
  offsetX.value = clampOffset(startOffset + dx);
};

const onMouseUp = () => {
  if (!tracking) return;
  tracking = false;
  isDragging.value = false;
  document.removeEventListener('mousemove', onMouseMove);
  document.removeEventListener('mouseup', onMouseUp);
  snap();
};

const onMouseDown = (e: MouseEvent) => {
  if (!props.deletable || e.button !== 0) return;
  const target = e.target as HTMLElement;
  if (target.closest('input, button, textarea, a, .el-checkbox')) return;

  isDragging.value = true;
  tracking = true;
  startX = e.clientX;
  startOffset = offsetX.value;
  document.addEventListener('mousemove', onMouseMove);
  document.addEventListener('mouseup', onMouseUp);
};

const onDelete = () => {
  emit('delete');
  offsetX.value = 0;
  emit('close');
};

onMounted(() => {
  measureRow();
  if (typeof ResizeObserver !== 'undefined' && rootRef.value) {
    resizeObserver = new ResizeObserver(measureRow);
    resizeObserver.observe(rootRef.value);
  }
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  document.removeEventListener('mousemove', onMouseMove);
  document.removeEventListener('mouseup', onMouseUp);
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.swipe-delete-row {
  position: relative;
  width: 100%;
  border-radius: $radius-card;
  overflow: hidden;
}

.swipe-track {
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  width: max-content;
  will-change: transform;
  transition: transform 0.28s cubic-bezier(0.32, 0.72, 0, 1);
  touch-action: pan-y;

  &.dragging {
    transition: none;
  }
}

.swipe-content {
  flex: 0 0 var(--swipe-row-w, 100%);
  width: var(--swipe-row-w, 100%);
  min-width: var(--swipe-row-w, 100%);
  box-sizing: border-box;
  background: $color-card;
}

.swipe-action {
  flex-shrink: 0;
  display: flex;
  align-items: stretch;
  justify-content: center;
  transform-origin: center center;
  transition: opacity 0.12s ease-out, transform 0.12s ease-out;

  .swipe-track.dragging & {
    transition: none;
  }
}

.swipe-del-btn {
  flex: 1;
  width: 100%;
  min-height: 100%;
  padding: 0;
  border: none;
  border-radius: 0 $radius-card $radius-card 0;
  background: $color-price;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  -webkit-tap-highlight-color: transparent;

  &:active {
    filter: brightness(0.92);
  }
}
</style>
