<template>
  <aside class="pc-user-sidebar ignore" aria-label="我的简选导航">
    <div class="sidebar-head">
      <RouterLink to="/account" class="sidebar-brand">我的简选</RouterLink>
    </div>
    <nav v-for="group in PC_USER_NAV_GROUPS" :key="group.title" class="sidebar-group">
      <h4 class="group-title">{{ group.title }}</h4>
      <button
        v-for="item in group.items"
        :key="item.path"
        type="button"
        class="sidebar-link"
        :class="{ active: isActive(item.path) }"
        @click="onNavClick(item.path)"
      >
        {{ item.label }}
      </button>
    </nav>
  </aside>
</template>

<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router';
import { useOpenAgent } from '@/composables/useOpenAgent';
import { PC_USER_NAV_GROUPS } from '@/constants/pcUserNav';

const route = useRoute();
const router = useRouter();
const { openAgent } = useOpenAgent();

const onNavClick = (path: string) => {
  if (path === '/ai-assistant') {
    openAgent();
    return;
  }
  router.push(path);
};

const isActive = (path: string) => {
  if (path === '/account') return route.path === '/account';
  if (path === '/orders') return route.path === '/orders' || route.path.startsWith('/order/');
  if (path === '/member-center') return route.path === '/member-center';
  if (path === '/notifications') return route.path === '/notifications';
  return route.path === path || route.path.startsWith(`${path}/`);
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-user-sidebar.ignore {
  width: 180px;
  flex-shrink: 0;
  padding: 12px 0;
  background: $color-card;
  border-radius: $radius-card;
  box-shadow: $shadow-card;
  align-self: flex-start;
  position: sticky;
  top: 16px;

  .sidebar-head {
    padding: 0 16px 12px;
    border-bottom: 1px solid $color-border-gray;
    margin-bottom: 8px;
  }

  .sidebar-brand {
    font-size: 16px;
    font-weight: 700;
    color: $color-primary;
    text-decoration: none;

    &:hover {
      color: $color-primary-hover-bright;
    }
  }

  .sidebar-group {
    padding: 8px 0;
  }

  .group-title {
    margin: 0;
    padding: 6px 16px;
    font-size: 12px;
    font-weight: 600;
    color: $color-text-muted;
  }

  .sidebar-link {
    display: block;
    width: 100%;
    padding: 8px 16px;
    border: none;
    background: transparent;
    text-align: left;
    font-size: 14px;
    cursor: pointer;
    color: $color-text-primary;
    text-decoration: none;
    border-left: 3px solid transparent;
    transition: color $transition-fast, background $transition-fast, border-color $transition-fast;

    &:hover {
      color: $color-primary;
      background: $color-cat-hover-bg;
    }

    &.active {
      color: $color-primary;
      font-weight: 600;
      background: $color-primary-soft;
      border-left-color: $color-primary;
    }
  }
}
</style>
