<template>
  <div class="sl-screen-nav-wrap ignore">
    <nav class="sl-screen-nav" aria-label="快捷导航">
      <ul class="sl-nav-links">
        <li v-for="item in navLinks" :key="item.path">
          <RouterLink :to="item.path">{{ item.label }}</RouterLink>
        </li>
      </ul>
    </nav>
  </div>
</template>

<script setup lang="ts">
import { RouterLink } from 'vue-router';


const navLinks = [
  { label: '精选好物', path: '/' },
  { label: '全部分类', path: '/search' },
  { label: '优惠券', path: '/coupons' },
  { label: '签到有礼', path: '/sign' },
  { label: '会员中心', path: '/member-center' },
  { label: '我的收藏', path: '/wishlist' },
  { label: '我的订单', path: '/orders' },
  { label: '智能客服', path: '/ai-assistant' }
];
</script>
