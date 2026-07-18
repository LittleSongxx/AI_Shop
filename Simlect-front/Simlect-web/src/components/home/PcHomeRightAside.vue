<template>
  <aside class="pc-home-aside ignore" aria-label="快捷服务">
    <div class="user-card">
      <el-avatar :size="48" :src="avatarUrl" class="user-avatar">
        {{ avatarLetter }}
      </el-avatar>
      <p v-if="authStore.isLoggedIn" class="greet">
        您好，<strong>{{ authStore.userInfo?.nickName || '用户' }}</strong>
      </p>
      <p v-else class="greet">登录后享更多优惠</p>
      <RouterLink
        v-if="!authStore.isLoggedIn"
        class="btn-login"
        to="/login"
      >
        立即登录
      </RouterLink>
      <template v-else>
        <p v-if="authStore.memberLevelName" class="member-line">{{ authStore.memberLevelName }} · 成长值 {{ authStore.memberGrowthValue }}</p>
        <RouterLink class="btn-login outline" to="/account">个人中心</RouterLink>
        <div class="member-links">
          <RouterLink to="/member-center" class="mini-link">会员中心</RouterLink>
          <RouterLink to="/notifications" class="mini-link">
            消息<span v-if="unreadCount" class="mini-badge">{{ unreadCount }}</span>
          </RouterLink>
        </div>
      </template>
    </div>

    <nav class="quick-grid" aria-label="功能入口">
      <button
        v-for="item in quickItems"
        :key="item.path"
        type="button"
        class="quick-item"
        @click="onQuickClick(item)"
      >
        <el-icon :size="20" class="quick-icon"><component :is="item.icon" /></el-icon>
        <span class="quick-label">{{ item.label }}</span>
      </button>
    </nav>

    <div class="link-row">
      <RouterLink v-for="link in textLinks" :key="link.path" :to="link.path" class="link-chip">
        {{ link.label }}
      </RouterLink>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { RouterLink, useRouter } from 'vue-router';
import { useUnreadCount } from '@/composables/useUnreadCount';
import {
  ChatDotRound,
  List,
  ShoppingCart,
  Star,
  Ticket,
  Present
} from '@element-plus/icons-vue';
import { useOpenAgent } from '@/composables/useOpenAgent';
import { useAuthStore } from '@/stores/auth';
import { resolveAvatarUrl } from '@/utils/image';

const router = useRouter();
const { openAgent } = useOpenAgent();

const onQuickClick = (item: { path: string }) => {
  if (item.path === '/ai-assistant') {
    openAgent();
    return;
  }
  router.push(item.path);
};
const authStore = useAuthStore();
const { unreadCount } = useUnreadCount();

const avatarUrl = computed(() => resolveAvatarUrl(authStore.userInfo?.avatar));
const avatarLetter = computed(() => (authStore.userInfo?.nickName || '访')[0]);

const quickItems = [
  { label: '订单', path: '/orders', icon: List },
  { label: '购物车', path: '/cart', icon: ShoppingCart },
  { label: '优惠券', path: '/coupons', icon: Ticket },
  { label: '收藏', path: '/wishlist', icon: Star },
  { label: '签到', path: '/sign', icon: Present },
  { label: '客服', path: '/ai-assistant', icon: ChatDotRound }
];

const textLinks = [
  { label: '领券中心', path: '/coupons' },
  { label: '我的券', path: '/my-coupons' },
  { label: '足迹', path: '/footprint' }
];

onMounted(async () => {
  if (!authStore.isLoggedIn) return;
  try {
    await authStore.loadMemberCenter();
  } catch {

  }
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-home-aside.ignore {
  width: 180px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;

  .user-card {
    padding: 14px 12px;
    background: $color-card;
    border: 1px solid $color-border-gray;
    border-radius: $radius-sm;
    text-align: center;
  }

  .user-avatar {
    margin: 0 auto 8px;
    background: $color-primary-soft;
    color: $color-primary;
    font-weight: 700;
  }

  .greet {
    margin: 0 0 10px;
    font-size: 12px;
    line-height: 1.4;
    color: $color-text-body;
    word-break: break-all;

    strong {
      color: $color-text-primary;
      font-weight: 600;
    }
  }

  .member-line {
    margin: 0 0 8px;
    font-size: 11px;
    color: $color-primary;
    line-height: 1.4;
  }

  .member-links {
    display: flex;
    gap: 8px;
    margin-top: 8px;
    justify-content: center;
  }

  .mini-link {
    font-size: 11px;
    color: $color-text-body;
    text-decoration: none;

    &:hover {
      color: $color-primary;
    }
  }

  .mini-badge {
    margin-left: 2px;
    color: $color-price;
    font-weight: 600;
  }

  .btn-login {
    display: block;
    width: 100%;
    padding: 7px 0;
    border-radius: $radius-xs;
    background: $color-primary;
    color: #fff;
    font-size: 13px;
    font-weight: 600;
    text-decoration: none;
    text-align: center;
    box-sizing: border-box;

    &.outline {
      background: #fff;
      border: 1px solid $color-primary;
      color: $color-primary;
    }
  }

  .quick-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 4px;
    padding: 10px 8px;
    background: $color-card;
    border: 1px solid $color-border-gray;
    border-radius: $radius-sm;
  }

  .quick-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    padding: 6px 2px;
    border: none;
    background: transparent;
    cursor: pointer;
    border-radius: $radius-xs;

    &:hover {
      background: $color-cat-hover-bg;
      color: $color-primary;
    }
  }

  .quick-icon {
    color: $color-text-body;
  }

  .quick-label {
    font-size: 11px;
    line-height: 1.2;
    color: $color-text-body;
    white-space: nowrap;
  }

  .link-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 10px;
    background: $color-card;
    border: 1px solid $color-border-gray;
    border-radius: $radius-sm;
  }

  .link-chip {
    flex: 1 1 auto;
    min-width: 0;
    padding: 4px 8px;
    font-size: 12px;
    line-height: 1.3;
    color: $color-text-body;
    text-decoration: none;
    text-align: center;
    white-space: nowrap;
    border-radius: $radius-xs;
    background: #fafafa;

    &:hover {
      color: $color-primary;
      background: $color-cat-hover-bg;
    }
  }
}
</style>
