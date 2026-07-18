<template>
  <div class="pc-layout ignore pc-surface" :class="{ 'is-home-page': isHomePage }">
    <SiteHeader :simlect-search="isHomePage" />
    <PcSimlectScreenNav v-if="isHomePage" />
    <nav v-else class="pc-nav ignore" aria-label="站点导航">
      <div class="pc-nav-inner">
        <RouterLink
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="pc-nav-link"
          :class="{ active: isActive(item.path) }"
        >
          <el-icon class="nav-icon" :size="18"><component :is="item.icon" /></el-icon>
          <span class="nav-label">{{ item.label }}</span>
        </RouterLink>
      </div>
    </nav>
    <main class="pc-main">
      <div class="pc-page-container">
        <RouterView v-slot="{ Component, route: viewRoute }">
          <KeepAlive :max="4">
            <component :is="Component" :key="viewRoute.path" class="page-view-root" />
          </KeepAlive>
        </RouterView>
      </div>
    </main>
    <AppFooter />
    <PcFloatToolbar />
    <NotificationPopup @click="handleNotificationClick" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import {
  Grid,
  HomeFilled,
  List,
  ShoppingCart,
  Ticket,
  User
} from '@element-plus/icons-vue';
import { RouterLink, RouterView, useRoute } from 'vue-router';
import AppFooter from '@/components/layout/AppFooter.vue';
import PcFloatToolbar from '@/components/layout/PcFloatToolbar.vue';
import SiteHeader from '@/components/layout/SiteHeader.vue';
import PcSimlectScreenNav from '@/components/home/PcSimlectScreenNav.vue';
import NotificationPopup from '@/components/business/NotificationPopup.vue';
import { useRouter } from 'vue-router';
import { useUnreadCount } from '@/composables/useUnreadCount';
import { navigateNotification, type NotificationData } from '@/utils/notification';

const route = useRoute();
const router = useRouter();
const { refreshUnreadCount } = useUnreadCount();
const isHomePage = computed(() => route.path === '/');

const navItems = [
  { path: '/', label: '首页', icon: HomeFilled },
  { path: '/search', label: '分类', icon: Grid },
  { path: '/coupons', label: '优惠券', icon: Ticket },
  { path: '/cart', label: '购物车', icon: ShoppingCart },
  { path: '/orders', label: '我的订单', icon: List },
  { path: '/account', label: '个人中心', icon: User }
];

const isActive = (path: string) => {
  if (path === '/') return route.path === '/';
  return route.path === path || route.path.startsWith(`${path}/`);
};

const handleNotificationClick = (notification: NotificationData) => {
  void navigateNotification(router, notification, { refreshUnread: refreshUnreadCount });
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-layout.ignore {
  min-height: 100vh;
  min-height: var(--app-vh, 100dvh);
  display: flex;
  flex-direction: column;
  background: $color-bg;

  .pc-nav {
    background: $color-card;
    border-bottom: none;
    box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
  }

  .pc-nav-inner {
    max-width: $content-width;
    margin: 0 auto;
    padding: 0 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-wrap: nowrap;
    gap: 4px;
    min-height: 40px;
    overflow: hidden;
  }

  .pc-nav-link {
    display: inline-flex;
    flex-direction: row;
    align-items: center;
    gap: 6px;
    padding: 8px 14px;
    font-size: 14px;
    font-weight: 500;
    color: $color-text-primary;
    text-decoration: none;
    white-space: nowrap;
    flex-shrink: 0;
    transition: color $transition-fast;

    .nav-label {
      white-space: nowrap;
    }

    .nav-icon {
      color: $color-text-muted;
      flex-shrink: 0;
    }

    &:hover {
      color: $color-primary;

      .nav-icon {
        color: $color-primary;
      }
    }

    &.active {
      color: $color-primary;
      font-weight: 600;

      .nav-icon {
        color: $color-primary;
      }
    }
  }

  .pc-main {
    flex: 1;
    padding: 16px 0 32px;
  }

  .pc-page-container {
    width: 100%;
    max-width: $content-max-width;
    margin: 0 auto;
    padding: 0 16px;
    box-sizing: border-box;
  }
}
</style>
