<template>
  <aside class="pc-float-toolbar ignore" aria-label="快捷工具">
    <RouterLink to="/account" class="tool-item" title="个人中心">
      <el-icon :size="20"><User /></el-icon>
      <span>我的</span>
    </RouterLink>
    <RouterLink to="/cart" class="tool-item" title="购物车">
      <el-badge :value="cartStore.cartCount" :hidden="!cartStore.cartCount" :max="99">
        <el-icon :size="20"><ShoppingCart /></el-icon>
      </el-badge>
      <span>购物车</span>
    </RouterLink>
    <RouterLink to="/wishlist" class="tool-item" title="收藏夹">
      <el-icon :size="20"><Star /></el-icon>
      <span>收藏</span>
    </RouterLink>
    <RouterLink to="/footprint" class="tool-item" title="足迹">
      <el-icon :size="20"><Clock /></el-icon>
      <span>足迹</span>
    </RouterLink>
    <RouterLink v-if="authStore.isLoggedIn" to="/notifications" class="tool-item" title="消息">
      <el-badge :value="unreadCount" :hidden="!unreadCount" :max="99">
        <el-icon :size="20"><Bell /></el-icon>
      </el-badge>
      <span>消息</span>
    </RouterLink>
    <button type="button" class="tool-item" title="智能客服" @click="openAgent()">
      <el-icon :size="20"><ChatDotRound /></el-icon>
      <span>客服</span>
    </button>
    <button type="button" class="tool-item" title="回到顶部" @click="scrollTop">
      <el-icon :size="20"><Top /></el-icon>
      <span>顶部</span>
    </button>
  </aside>
</template>

<script setup lang="ts">
import { onMounted } from 'vue';
import { RouterLink } from 'vue-router';
import { Bell, ChatDotRound, Clock, ShoppingCart, Star, Top, User } from '@element-plus/icons-vue';
import { useUnreadCount } from '@/composables/useUnreadCount';
import { useOpenAgent } from '@/composables/useOpenAgent';
import { useAuthStore } from '@/stores/auth';
import { useCartStore } from '@/stores/cart';

const { openAgent } = useOpenAgent();

const authStore = useAuthStore();
const cartStore = useCartStore();
const { unreadCount } = useUnreadCount();

const scrollTop = () => {
  window.scrollTo({ top: 0, behavior: 'smooth' });
};

onMounted(() => {
  if (authStore.isLoggedIn) cartStore.fetchCartCount();
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-float-toolbar.ignore {
  position: fixed;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  z-index: 900;
  width: 54px;
  padding: 8px 0;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid $color-border-gray;
  border-right: none;
  border-radius: $radius-sm 0 0 $radius-sm;
  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.06);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;

  .tool-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    width: 100%;
    padding: 8px 4px;
    border: none;
    background: transparent;
    color: $color-text-body;
    font-size: 10px;
    text-decoration: none;
    cursor: pointer;
    transition: color $transition-fast, background $transition-fast;

    &:hover {
      color: $color-primary;
      background: $color-cat-hover-bg;
    }
  }
}
</style>
