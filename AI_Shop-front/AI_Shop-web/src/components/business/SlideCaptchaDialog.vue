<template>
  <Teleport to="body">
    <Transition name="slide-captcha-fade">
      <div v-if="visible" class="slide-captcha-overlay" @click.self="cancel">
        <div class="slide-captcha-panel" role="dialog" aria-modal="true" aria-label="安全验证">
          <div class="panel-glow" aria-hidden="true" />

          <header class="panel-header">
            <div class="header-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path
                  d="M12 2l7 4v6c0 5-3 9-7 10C8 21 5 17 5 12V6l7-4z"
                  stroke-linejoin="round"
                />
                <path d="M9.5 12.5l1.8 1.8 3.5-4.2" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </div>
            <div class="header-text">
              <h3>安全验证</h3>
              <p>拖动滑块完成拼图</p>
            </div>
            <button type="button" class="btn-close" aria-label="关闭" @click="cancel">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M6 6l12 12M18 6L6 18" stroke-linecap="round" />
              </svg>
            </button>
          </header>

          <div class="panel-body">
            <div
              ref="imageWrapRef"
              class="image-wrap"
              :class="{ success: status === 'success', fail: status === 'fail', dragging }"
              @pointerdown="onDragStart"
              @touchstart="onTouchStart"
            >
              <div v-if="loading" class="image-skeleton">
                <span class="skeleton-shimmer" />
                <p>加载验证图…</p>
              </div>
              <template v-else>
                <img v-if="bgSrc" :src="bgSrc" class="bg-img" alt="" draggable="false" />
                <img
                  v-if="blockSrc"
                  :src="blockSrc"
                  class="block-img"
                  :style="{ transform: `translateX(${moveX}px)` }"
                  alt=""
                  draggable="false"
                />
                <div v-if="status === 'success'" class="status-badge success-badge">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                    <path d="M5 12.5l4.2 4.3L19 7" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                  验证通过
                </div>
              </template>
              <button
                type="button"
                class="btn-refresh"
                :disabled="loading || verifying"
                title="换一张"
                @click="loadCaptcha"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M4 12a8 8 0 0 1 13.7-5.7M20 12a8 8 0 0 1-13.7 5.7" stroke-linecap="round" />
                  <path d="M16 4h4V0M4 20h4v4" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </button>
            </div>

            <div
              ref="sliderWrapRef"
              class="slider-wrap"
              @pointerdown="onDragStart"
              @touchstart="onTouchStart"
            >
              <div class="slider-track">
                <div class="slider-fill" :style="{ width: `${Math.max(0, moveX + knobSize / 2)}px` }" />
                <span class="slider-hint" :class="{ hidden: dragging || moveX > 4 }">
                  {{ hintText }}
                </span>
              </div>
              <div
                ref="knobRef"
                class="slider-knob"
                :class="{ dragging, success: status === 'success', fail: status === 'fail' }"
                :style="{ transform: `translateX(${moveX}px)` }"
              >
                <svg v-if="status === 'success'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                  <path d="M5 12.5l4.2 4.3L19 7" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                <svg v-else-if="status === 'fail'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                  <path d="M6 6l12 12M18 6L6 18" stroke-linecap="round" />
                </svg>
                <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M5 12h14M13 6l6 6-6 6" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </div>
            </div>

            <p v-if="tip" class="tip" :class="status">{{ tip }}</p>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue';
import { checkSlideCaptcha, cancelSlideCaptchaToken, fetchSlideCaptcha, toCaptchaX, type CaptchaGetData } from '@/utils/slideCaptcha';

type Status = 'idle' | 'success' | 'fail';

const visible = ref(false);
const loading = ref(false);
const verifying = ref(false);
const dragging = ref(false);
const moveX = ref(0);
const status = ref<Status>('idle');
const tip = ref('');
const hintText = computed(() => (status.value === 'fail' ? '请重新拖动滑块' : '向右拖动滑块完成拼图'));

const imageWrapRef = ref<HTMLElement | null>(null);
const sliderWrapRef = ref<HTMLElement | null>(null);
const knobRef = ref<HTMLElement | null>(null);
const knobSize = 46;

const bgSrc = ref('');
const blockSrc = ref('');
const captchaMeta = ref<CaptchaGetData | null>(null);
const startTime = ref(0);
const trackWidth = ref(0);

let resolveFn: ((v: string) => void) | null = null;
let rejectFn: (() => void) | null = null;
let startClientX = 0;
let startMoveX = 0;
let settled = false;
let activePointerId: number | null = null;
let touchDragActive = false;

const toImgSrc = (b64: string) => (b64.startsWith('data:') ? b64 : `data:image/png;base64,${b64}`);

const resetState = () => {
  moveX.value = 0;
  status.value = 'idle';
  tip.value = '';
  dragging.value = false;
  verifying.value = false;
};

const discardCurrentToken = () => {
  const token = captchaMeta.value?.token;
  if (token) {
    cancelSlideCaptchaToken(token);
  }
};

const loadCaptcha = async () => {
  discardCurrentToken();
  loading.value = true;
  resetState();
  bgSrc.value = '';
  blockSrc.value = '';
  try {
    const data = await fetchSlideCaptcha();
    captchaMeta.value = data;
    bgSrc.value = toImgSrc(data.originalImageBase64);
    blockSrc.value = toImgSrc(data.jigsawImageBase64);
    await nextTick();
    measureTrack();
  } catch (e: unknown) {
    tip.value = e instanceof Error ? e.message : '加载失败';
    status.value = 'fail';
  } finally {
    loading.value = false;
  }
};

const open = (): Promise<string> => {
  if (visible.value) {
    return Promise.reject(new Error('验证窗口已打开'));
  }
  settled = false;
  visible.value = true;
  loadCaptcha();
  return new Promise((resolve, reject) => {
    resolveFn = resolve;
    rejectFn = reject;
  });
};

const finish = (token: string) => {
  if (settled) return;
  settled = true;
  status.value = 'success';
  const seconds = ((Date.now() - startTime.value) / 1000).toFixed(1);
  tip.value = `${seconds}s 验证成功`;
  setTimeout(() => {
    visible.value = false;
    resolveFn?.(token);
    cleanup();
  }, 650);
};

const cancel = () => {
  if (settled) return;
  settled = true;
  discardCurrentToken();
  visible.value = false;
  rejectFn?.();
  cleanup();
};

const cleanup = () => {
  resolveFn = null;
  rejectFn = null;
  resetState();
  captchaMeta.value = null;
  bgSrc.value = '';
  blockSrc.value = '';
};

const measureTrack = () => {
  trackWidth.value = sliderWrapRef.value?.clientWidth || imageWrapRef.value?.clientWidth || 310;
};

const maxMove = () => Math.max(0, trackWidth.value - knobSize);

const canDrag = () => !loading.value && !verifying.value && status.value !== 'success';

const updateMove = (clientX: number) => {
  const delta = clientX - startClientX;
  moveX.value = Math.min(maxMove(), Math.max(0, startMoveX + delta));
};

const bindDragListeners = () => {
  window.addEventListener('pointermove', onPointerMove, { passive: false });
  window.addEventListener('pointerup', onPointerUp);
  window.addEventListener('pointercancel', onPointerUp);
  window.addEventListener('touchmove', onTouchMove, { passive: false });
  window.addEventListener('touchend', onTouchEnd);
  window.addEventListener('touchcancel', onTouchEnd);
};

const unbindDragListeners = () => {
  window.removeEventListener('pointermove', onPointerMove);
  window.removeEventListener('pointerup', onPointerUp);
  window.removeEventListener('pointercancel', onPointerUp);
  window.removeEventListener('touchmove', onTouchMove);
  window.removeEventListener('touchend', onTouchEnd);
  window.removeEventListener('touchcancel', onTouchEnd);
};

const beginDrag = (clientX: number) => {
  if (!canDrag() || dragging.value) return false;
  measureTrack();
  dragging.value = true;
  status.value = 'idle';
  tip.value = '';
  startTime.value = Date.now();
  startClientX = clientX;
  startMoveX = moveX.value;
  bindDragListeners();
  return true;
};

const onDragStart = (e: PointerEvent) => {
  if (!canDrag()) return;
  if ((e.target as HTMLElement).closest('.btn-refresh')) return;
  e.preventDefault();
  e.stopPropagation();
  if (!beginDrag(e.clientX)) return;
  activePointerId = e.pointerId;
  touchDragActive = e.pointerType === 'touch';
  knobRef.value?.setPointerCapture?.(e.pointerId);
};

const onTouchStart = (e: TouchEvent) => {
  if (!canDrag() || dragging.value) return;
  if ((e.target as HTMLElement).closest('.btn-refresh')) return;
  const touch = e.touches[0];
  if (!touch) return;
  if (!beginDrag(touch.clientX)) return;
  touchDragActive = true;
  e.preventDefault();
};

const onPointerMove = (e: PointerEvent) => {
  if (!dragging.value) return;
  if (activePointerId != null && e.pointerId !== activePointerId) return;
  e.preventDefault();
  updateMove(e.clientX);
};

const onTouchMove = (e: TouchEvent) => {
  if (!dragging.value || !touchDragActive) return;
  const touch = e.touches[0];
  if (!touch) return;
  e.preventDefault();
  updateMove(touch.clientX);
};

const endDrag = async () => {
  if (!dragging.value) return;
  dragging.value = false;
  activePointerId = null;
  touchDragActive = false;
  unbindDragListeners();

  const meta = captchaMeta.value;
  if (!meta?.token) return;

  verifying.value = true;
  try {
    const captchaX = toCaptchaX(moveX.value, trackWidth.value);
    const captchaVerification = await checkSlideCaptcha({
      token: meta.token,
      secretKey: meta.secretKey,
      moveX: captchaX
    });
    finish(captchaVerification);
  } catch (e: unknown) {
    status.value = 'fail';
    tip.value = e instanceof Error ? e.message : '验证失败';
    setTimeout(() => {
      resetState();
      loadCaptcha();
    }, 900);
  } finally {
    verifying.value = false;
  }
};

const onPointerUp = (e: PointerEvent) => {
  if (touchDragActive) return;
  if (activePointerId != null && e.pointerId !== activePointerId) return;
  void endDrag();
};

const onTouchEnd = () => {
  if (!touchDragActive) return;
  void endDrag();
};

onBeforeUnmount(() => {
  discardCurrentToken();
  unbindDragListeners();
});

defineExpose({ open });
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.slide-captcha-overlay {
  position: fixed;
  inset: 0;
  z-index: 4000;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(15, 15, 18, 0.55);
  backdrop-filter: blur(10px);
  touch-action: none;
  overscroll-behavior: contain;
}

.slide-captcha-panel {
  position: relative;
  width: min(92vw, 380px);
  border-radius: 8px;
  background: linear-gradient(165deg, #fff 0%, #fafafa 100%);
  border: 1px solid rgba(255, 255, 255, 0.8);
  box-shadow:
    0 24px 64px rgba(0, 0, 0, 0.22),
    0 0 0 1px rgba(29, 29, 31, 0.04);
  overflow: hidden;
}

.panel-glow {
  display: none;
}

.panel-header {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 20px 20px 14px;
}

.header-icon {
  width: 42px;
  height: 42px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: 8px;
  color: $color-gold;
  background: linear-gradient(145deg, $color-gold-soft, #fff);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);

  svg {
    width: 22px;
    height: 22px;
  }
}

.header-text {
  flex: 1;
  min-width: 0;

  h3 {
    margin: 0;
    font-size: 17px;
    font-weight: 600;
    color: $color-text-title;
    letter-spacing: 0;
  }

  p {
    margin: 4px 0 0;
    font-size: 12px;
    line-height: 1.45;
    color: $color-text-muted;
  }
}

.btn-close {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: $color-text-muted;
  cursor: pointer;
  display: grid;
  place-items: center;

  svg {
    width: 18px;
    height: 18px;
  }

  &:hover {
    background: $color-primary-muted;
    color: $color-text-title;
  }
}

.panel-body {
  padding: 0 20px 20px;
}

.image-wrap {
  position: relative;
  width: 100%;
  aspect-ratio: 310 / 155;
  border-radius: 8px;
  overflow: hidden;
  background: #1a1a1e;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.06);
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
  cursor: grab;

  &.dragging {
    cursor: grabbing;
  }

  &.success {
    box-shadow: 0 0 0 2px rgba($color-success, 0.45);
  }

  &.fail {
    animation: shake 0.45s ease;
  }
}

@keyframes shake {
  0%,
  100% {
    transform: translateX(0);
  }
  25% {
    transform: translateX(-4px);
  }
  75% {
    transform: translateX(4px);
  }
}

.bg-img,
.block-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  user-select: none;
  pointer-events: none;
}

.block-img {
  width: auto;
  max-width: none;
  transition: transform 0.05s linear;
}

.image-skeleton {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: rgba(255, 255, 255, 0.65);
  font-size: 13px;
  background: linear-gradient(135deg, #2a2a30, #1a1a1e);

  .skeleton-shimmer {
    width: 48px;
    height: 48px;
    border-radius: 50%;
    border: 3px solid rgba(255, 255, 255, 0.12);
    border-top-color: $color-gold;
    animation: spin 0.8s linear infinite;
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.btn-refresh {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 34px;
  height: 34px;
  border: none;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  cursor: pointer;
  backdrop-filter: blur(6px);
  display: grid;
  place-items: center;
  touch-action: manipulation;
  z-index: 2;

  svg {
    width: 16px;
    height: 16px;
  }

  &:hover:not(:disabled) {
    background: rgba(0, 0, 0, 0.62);
  }

  &:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
}

.status-badge {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #fff;
  background: rgba(82, 196, 26, 0.72);
  backdrop-filter: blur(2px);

  svg {
    width: 22px;
    height: 22px;
  }
}

.slider-wrap {
  position: relative;
  margin-top: 14px;
  height: 46px;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}

.slider-track {
  position: absolute;
  inset: 0;
  border-radius: 8px;
  background: $color-bg-subtle;
  border: 1px solid $color-border-light;
  overflow: hidden;
}

.slider-fill {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  border-radius: 23px 0 0 23px;
  background: linear-gradient(90deg, rgba($color-gold, 0.25), rgba($color-gold, 0.08));
  pointer-events: none;
  transition: width 0.05s linear;
}

.slider-hint {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  font-size: 13px;
  color: $color-text-muted;
  pointer-events: none;
  transition: opacity 0.2s;

  &.hidden {
    opacity: 0;
  }
}

.slider-knob {
  position: relative;
  z-index: 1;
  width: 46px;
  height: 46px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  cursor: grab;
  color: $color-primary;
  background: linear-gradient(180deg, #fff 0%, #f3f3f5 100%);
  box-shadow:
    0 4px 14px rgba(0, 0, 0, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.95);
  touch-action: none;
  transition: box-shadow 0.2s, color 0.2s, background 0.2s;

  svg {
    width: 18px;
    height: 18px;
  }

  &.dragging {
    cursor: grabbing;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.18);
  }

  &.success {
    color: #fff;
    background: linear-gradient(145deg, #6dd400, $color-success);
  }

  &.fail {
    color: #fff;
    background: linear-gradient(145deg, #ff7875, $color-error);
  }
}

.tip {
  margin: 10px 0 0;
  min-height: 18px;
  text-align: center;
  font-size: 12px;
  color: $color-text-muted;

  &.success {
    color: $color-success;
  }

  &.fail {
    color: $color-error;
  }
}

.slide-captcha-fade-enter-active,
.slide-captcha-fade-leave-active {
  transition: opacity 0.25s ease;
}

.slide-captcha-fade-enter-from,
.slide-captcha-fade-leave-to {
  opacity: 0;
}

.slide-captcha-fade-enter-active .slide-captcha-panel,
.slide-captcha-fade-leave-active .slide-captcha-panel {
  transition: transform 0.28s cubic-bezier(0.34, 1.2, 0.64, 1);
}

.slide-captcha-fade-enter-from .slide-captcha-panel,
.slide-captcha-fade-leave-to .slide-captcha-panel {
  transform: translateY(16px) scale(0.96);
}
</style>
