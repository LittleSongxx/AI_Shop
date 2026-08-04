<template>
  <div class="pc-auth-shell ignore">
    <header class="pc-auth-header">
      <RouterLink to="/" class="brand">
        <BrandMark class="brand-icon" />
        <span class="brand-text">智选</span>
      </RouterLink>
      <nav class="auth-links">
        <RouterLink to="/">网站首页</RouterLink>
        <RouterLink v-if="!isLoginPage" to="/login">登录</RouterLink>
        <RouterLink v-if="isLoginPage" to="/register">免费注册</RouterLink>
      </nav>
    </header>
    <main class="pc-auth-main">
      <slot />
    </main>
    <footer class="pc-auth-footer">
      <span>© 智选 SmartSelect</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import BrandMark from '@/components/common/BrandMark.vue';

const route = useRoute();
const isLoginPage = computed(() => route.path === '/login');
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-auth-shell.ignore {
  min-height: 100vh;
  min-height: var(--app-vh, 100dvh);
  display: flex;
  flex-direction: column;
  background: $color-bg;

  .pc-auth-header {
    height: 62px;
    padding: 0 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: $color-card;
    border-bottom: 1px solid $color-border-gray;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 8px;
    text-decoration: none;

    .brand-icon {
      width: 38px;
      height: 38px;
    }

    .brand-text {
      font-size: 21px;
      font-weight: 600;
      color: $color-primary;
      letter-spacing: 0;
    }
  }

  .auth-links {
    display: flex;
    gap: 20px;
    font-size: 13px;

    a {
      color: $color-text-body;
      text-decoration: none;

      &:hover {
        color: $color-primary;
      }
    }
  }

  .pc-auth-main {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 40px 16px;
  }

  .pc-auth-footer {
    padding: 16px;
    text-align: center;
    font-size: 12px;
    color: $color-text-muted;
  }
}
</style>
