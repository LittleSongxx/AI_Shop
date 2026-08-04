<template>
  <LiquidGlassSurface tag="header" intensity="medium" class="home-search-header ignore">
    <div class="home-search-header__row">
      <div class="search-bar">
        <button type="button" class="search-field" @click="goSearchPortal">
          <el-icon class="search-icon" :size="19"><Search /></el-icon>
          <span class="placeholder">{{ placeholder }}</span>
        </button>
      </div>
      <div class="home-search-header__actions">
        <button
          v-if="authStore.isLoggedIn"
          type="button"
          class="icon-btn"
          aria-label="消息"
          @click="goNotifications"
        >
          <el-badge :value="unreadCount" :hidden="!unreadCount" :max="99">
            <el-icon :size="20"><Bell /></el-icon>
          </el-badge>
        </button>
        <button type="button" class="icon-btn" aria-label="智能客服" @click="goAgent">
          <el-icon :size="20"><ChatDotRound /></el-icon>
        </button>
      </div>
    </div>
    <CategoryNavCard />
  </LiquidGlassSurface>
</template>

<script setup lang="ts">
import LiquidGlassSurface from '@/components/common/LiquidGlassSurface.vue';
import CategoryNavCard from '@/components/business/CategoryNavCard.vue';
import { useRouter } from 'vue-router';
import { Bell, ChatDotRound, Search } from '@element-plus/icons-vue';
import { useOpenAgent } from '@/composables/useOpenAgent';
import { useUnreadCount } from '@/composables/useUnreadCount';
import { useAuthStore } from '@/stores/auth';

withDefaults(
  defineProps<{
    placeholder?: string;
  }>(),
  { placeholder: '搜索商品/品牌' }
);

const router = useRouter();
const { openAgent } = useOpenAgent();
const authStore = useAuthStore();
const { unreadCount } = useUnreadCount();

const goSearchPortal = () => router.push('/search-portal');
const goNotifications = () => router.push('/notifications');
const goAgent = () => openAgent();
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.home-search-header {
  flex-shrink: 0;
  z-index: 1001;
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  background: #fff;
  padding: 0;
  padding-top: env(safe-area-inset-top, 0);
  padding-bottom: 6px;
  min-height: calc($home-search-bar-height + env(safe-area-inset-top, 0));
  border-radius: 0;
  border-bottom: 1px solid var(--glass-border-soft);
  box-shadow: var(--glass-shadow-sm);

  :deep(.liquid-glass-surface__content) {
    width: 100%;
  }
}

.home-search-header__row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  margin-top: 10px;
  padding: 0 $app-page-gutter;
}

.search-bar {
  flex: 1;
  min-width: 0;
}

.search-field {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 42px;
  padding: 0 16px;
  border: 1px solid rgba(120, 120, 128, 0.12);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.55);
  cursor: pointer;
  text-align: left;
  transition: background 0.2s ease, border-color 0.2s ease;

  &:active {
    background: rgba(255, 255, 255, 0.72);
    border-color: rgba(120, 120, 128, 0.18);
  }

  .search-icon {
    flex-shrink: 0;
    color: $color-text-muted;
  }

  .placeholder {
    flex: 1;
    font-size: 15px;
    color: $color-text-muted;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.home-search-header__actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.icon-btn {
  width: 40px;
  height: 40px;
  padding: 0;
  border: none;
  background: rgba(255, 255, 255, 0.38);
  color: $color-text-body;
  display: grid;
  place-items: center;
  cursor: pointer;
  border-radius: 8px;
  transition: background 0.2s ease, transform 0.15s ease;

  &:active {
    background: rgba(255, 255, 255, 0.62);
    transform: scale(0.96);
  }
}
</style>
