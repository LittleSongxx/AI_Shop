<template>
  <Transition name="ios-notification">
    <div v-if="visible" class="ios-notification-host">
      <div
        class="ios-notification-shell"
        :class="{ dragging: isDragging }"
        :style="dragStyle"
      >
        <LiquidGlassSurface
          intensity="subtle"
          class="ios-notification-banner"
          role="button"
          tabindex="0"
          @click="handleBannerClick"
          @keydown.enter="handleBannerClick"
          @touchstart.passive="onTouchStart"
          @touchmove.passive="onTouchMove"
          @touchend="onTouchEnd"
        >
          <div class="ios-notification-inner">
            <div class="ios-notification-icon" :class="`ios-notification-icon--${iconVisual.theme}`">
              <BrandMark
                v-if="iconVisual.useBrand"
                class="ios-app-mark"
                variant="light"
              />
              <el-icon v-else class="ios-type-icon">
                <component :is="iconVisual.icon" />
              </el-icon>
            </div>

            <div class="ios-notification-copy">
              <div class="ios-notification-meta">
                <span class="ios-app-name">简选</span>
                <span class="ios-meta-sep" aria-hidden="true">·</span>
                <span class="ios-meta-time">{{ formatTime(notification.createTime) }}</span>
              </div>
              <p class="ios-notification-title">{{ notification.title }}</p>
              <p class="ios-notification-body">{{ notification.content }}</p>
            </div>

            <span class="ios-notification-chevron" aria-hidden="true">
              <el-icon :size="14"><ArrowRight /></el-icon>
            </span>
          </div>
        </LiquidGlassSurface>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, type Component } from 'vue';
import {
  AlarmClock,
  ArrowRight,
  Box,
  Calendar,
  ShoppingBag,
  StarFilled,
  Ticket
} from '@element-plus/icons-vue';
import BrandMark from '@/components/common/BrandMark.vue';
import LiquidGlassSurface from '@/components/common/LiquidGlassSurface.vue';
import { notificationApi } from '@/api/modules';
import { useAuthStore } from '@/stores/auth';
import type { NotificationData } from '@/utils/notification';
import { formatDisplayDateTime } from '@/utils/formatDateTime';

interface Notification {
  notificationId: string;
  title: string;
  content: string;
  bizType?: string;
  bizId?: string;
  createTime?: string;
}

type IconTheme = 'app' | 'logistics' | 'coupon' | 'coupon-warn' | 'order' | 'member' | 'sign';

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'click', notification: Notification): void;
}>();

const shownNotificationIds = new Set<string>();
let globalPollInitialized = false;

const visible = ref(false);
const notification = ref<Notification>({
  notificationId: '',
  title: '',
  content: ''
});

const iconVisual = computed((): { theme: IconTheme; useBrand: boolean; icon: Component } => {
  const bizType = notification.value.bizType || '';

  if (bizType === 'rush_coupon' || bizType === 'coupon_rush') {
    return { theme: 'coupon', useBrand: false, icon: Ticket };
  }
  if (bizType === 'coupon_expire' || bizType === 'coupon') {
    return { theme: 'coupon-warn', useBrand: false, icon: AlarmClock };
  }
  if (bizType === 'logistics') {
    return { theme: 'logistics', useBrand: false, icon: Box };
  }
  if (bizType === 'order') {
    return { theme: 'order', useBrand: false, icon: ShoppingBag };
  }
  if (bizType === 'member_level') {
    return { theme: 'member', useBrand: false, icon: StarFilled };
  }
  if (bizType === 'sign') {
    return { theme: 'sign', useBrand: false, icon: Calendar };
  }
  return { theme: 'app', useBrand: true, icon: Ticket };
});

const startX = ref(0);
const startY = ref(0);
const currentX = ref(0);
const currentY = ref(0);
const isDragging = ref(false);
const translateX = ref(0);
const translateY = ref(0);
const didSwipeDismiss = ref(false);

const dragStyle = computed(() => {
  const scale = isDragging.value ? Math.max(0.94, 1 + translateY.value / 400) : 1;
  return {
    transform: `translate3d(${translateX.value}px, ${translateY.value}px, 0) scale(${scale})`
  };
});

const show = (data: Notification) => {
  if (!data.notificationId) {
    return;
  }
  if (shownNotificationIds.has(data.notificationId)) {
    return;
  }
  shownNotificationIds.add(data.notificationId);
  notification.value = data;
  visible.value = true;
  startAutoCloseTimer();

  notificationApi.clearPopupNotification(data.notificationId).catch(() => {});
};

const close = () => {
  visible.value = false;
  stopAutoCloseTimer();

  if (notification.value.notificationId) {
    notificationApi.clearPopupNotification(notification.value.notificationId).catch((e) => {
      console.error('清除未弹窗通知标记失败', e);
    });
  }

  emit('close');
};

let autoCloseTimer: ReturnType<typeof setTimeout> | null = null;

const startAutoCloseTimer = () => {
  stopAutoCloseTimer();
  autoCloseTimer = setTimeout(() => {
    if (visible.value) close();
  }, 5000);
};

const stopAutoCloseTimer = () => {
  if (autoCloseTimer) {
    clearTimeout(autoCloseTimer);
    autoCloseTimer = null;
  }
};

const handleBannerClick = () => {
  if (didSwipeDismiss.value) {
    didSwipeDismiss.value = false;
    return;
  }
  emit('click', notification.value);
  close();
};

const formatTime = (time?: string) => {
  if (!time) return '现在';
  const now = new Date();
  const date = new Date(time);
  const diff = now.getTime() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);

  if (minutes < 1) return '现在';
  if (minutes < 60) return `${minutes}分钟前`;
  if (hours < 24) return `${hours}小时前`;
  return formatDisplayDateTime(time);
};

const onTouchStart = (e: TouchEvent) => {
  const touch = e.touches[0];
  startX.value = touch.clientX;
  startY.value = touch.clientY;
  currentX.value = touch.clientX;
  currentY.value = touch.clientY;
  isDragging.value = true;
  didSwipeDismiss.value = false;
  stopAutoCloseTimer();
};

const onTouchMove = (e: TouchEvent) => {
  if (!isDragging.value) return;
  const touch = e.touches[0];
  currentX.value = touch.clientX;
  currentY.value = touch.clientY;

  const deltaX = currentX.value - startX.value;
  const deltaY = currentY.value - startY.value;

  translateX.value = Math.max(-36, Math.min(36, deltaX * 0.35));
  translateY.value = Math.max(-72, Math.min(8, deltaY));
};

const onTouchEnd = () => {
  if (!isDragging.value) return;

  const deltaX = startX.value - currentX.value;
  const deltaY = startY.value - currentY.value;
  const threshold = 44;

  if (deltaX > threshold || deltaY > threshold) {
    didSwipeDismiss.value = true;
    close();
  } else {
    startAutoCloseTimer();
  }

  isDragging.value = false;
  startX.value = 0;
  startY.value = 0;
  currentX.value = 0;
  currentY.value = 0;
  translateX.value = 0;
  translateY.value = 0;
};

const loadPopupNotification = async () => {
  const authStore = useAuthStore();
  if (!authStore.isLoggedIn) return;

  try {
    const result = await notificationApi.getPopupNotification();
    if (result?.notificationId) {
      show(result);
    }
  } catch (e) {
    console.error('获取未弹窗通知失败', e);
  }
};

const ensureGlobalPoll = () => {
  if (globalPollInitialized) return;
  globalPollInitialized = true;
  setInterval(loadPopupNotification, 60000);
  setTimeout(loadPopupNotification, 2000);
};

const onRealtimeNotification = (event: Event) => {
  const detail = (event as CustomEvent<NotificationData>).detail;
  if (detail?.notificationId) {
    show(detail);
  }
};

onMounted(() => {
  window.addEventListener('newNotification', onRealtimeNotification as EventListener);
  ensureGlobalPoll();
});

onUnmounted(() => {
  window.removeEventListener('newNotification', onRealtimeNotification as EventListener);
  stopAutoCloseTimer();
});

defineExpose({ show });
</script>

<style lang="scss" scoped>
@use '@/styles/variables' as *;

.ios-notification-host {
  position: fixed;
  inset: 0 auto auto 0;
  width: 100%;
  z-index: 100000;
  padding: calc(env(safe-area-inset-top, 0px) + 10px) 10px 0;
  pointer-events: none;
}

.ios-notification-shell {
  width: 100%;
  max-width: 420px;
  margin: 0 auto;
  pointer-events: auto;
  transform-origin: top center;
  transition: transform 0.28s cubic-bezier(0.25, 0.1, 0.25, 1);

  &.dragging {
    transition: none;
  }
}

.ios-notification-banner {
  width: 100%;
  border-radius: 22px;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  box-shadow:
    0 0 0 0.5px rgba(255, 255, 255, 0.55) inset,
    0 10px 40px rgba(0, 0, 0, 0.14),
    0 2px 10px rgba(0, 0, 0, 0.08);
  transition: transform 0.18s ease, box-shadow 0.18s ease;

  &:active {
    transform: scale(0.985);
  }
}

.ios-notification-inner {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 13px 14px 13px 13px;
}

.ios-notification-icon {
  flex-shrink: 0;
  width: 38px;
  height: 38px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  overflow: hidden;
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.18),
    inset 0 1px 0 rgba(255, 255, 255, 0.28);

  &--app {
    background: linear-gradient(160deg, #3a3a3c 0%, #1c1c1e 55%, #2c2c2e 100%);
  }

  &--logistics {
    background: linear-gradient(160deg, #64d2ff 0%, #0a84ff 100%);
  }

  &--coupon {
    background: linear-gradient(160deg, #ffe066 0%, #ff9f0a 100%);
  }

  &--coupon-warn {
    background: linear-gradient(160deg, #ffb340 0%, #ff6723 100%);
  }

  &--order {
    background: linear-gradient(160deg, #bf5af2 0%, #5e5ce6 100%);
  }

  &--member {
    background: linear-gradient(160deg, #ffd426 0%, #c9a962 100%);
  }

  &--sign {
    background: linear-gradient(160deg, #63e6a8 0%, #30d158 100%);
  }
}

.ios-app-mark {
  width: 22px;
  height: 22px;
}

.ios-type-icon {
  font-size: 20px;
  color: #fff;
  filter: drop-shadow(0 1px 1px rgba(0, 0, 0, 0.12));
}

.ios-notification-copy {
  flex: 1;
  min-width: 0;
  padding-top: 1px;
}

.ios-notification-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 2px;
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', sans-serif;
  font-size: 13px;
  line-height: 1.2;
  color: rgba(60, 60, 67, 0.6);
  letter-spacing: -0.01em;
}

.ios-app-name {
  font-weight: 600;
  color: rgba(60, 60, 67, 0.72);
}

.ios-meta-sep {
  opacity: 0.55;
}

.ios-meta-time {
  font-weight: 400;
}

.ios-notification-title {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'PingFang SC', 'Helvetica Neue', sans-serif;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.25;
  letter-spacing: -0.02em;
  color: #1d1d1f;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ios-notification-body {
  margin: 2px 0 0;
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', sans-serif;
  font-size: 15px;
  font-weight: 400;
  line-height: 1.32;
  letter-spacing: -0.01em;
  color: rgba(60, 60, 67, 0.88);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.ios-notification-chevron {
  flex-shrink: 0;
  align-self: center;
  color: rgba(60, 60, 67, 0.28);
  opacity: 0;
  transition: opacity 0.2s ease;

  @media (min-width: 768px) {
    opacity: 1;
  }
}

@media (min-width: 768px) {
  .ios-notification-host {
    padding-top: calc(env(safe-area-inset-top, 0px) + 16px);
    padding-inline: 16px;
  }

  .ios-notification-shell {
    max-width: 390px;
  }

  .ios-notification-inner {
    padding: 14px 16px 14px 14px;
  }
}

.ios-notification-enter-active .ios-notification-shell,
.ios-notification-leave-active .ios-notification-shell {
  transition:
    transform 0.42s cubic-bezier(0.22, 1, 0.36, 1),
    opacity 0.32s ease;
}

.ios-notification-enter-from .ios-notification-shell,
.ios-notification-leave-to .ios-notification-shell {
  transform: translate3d(0, calc(-100% - 18px), 0) scale(0.96);
  opacity: 0;
}

.ios-notification-enter-to .ios-notification-shell,
.ios-notification-leave-from .ios-notification-shell {
  transform: translate3d(0, 0, 0) scale(1);
  opacity: 1;
}
</style>
