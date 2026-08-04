<template>
  <div class="home-feature-cards">
    <section class="feature-grid">
      <button
        v-for="item in featureItems"
        :key="item.path"
        type="button"
        class="feature-card"
        @click="handleClick(item)"
      >
        <el-icon class="feature-icon" :style="{ color: iconColor }" :size="22">
          <component :is="item.icon" />
        </el-icon>
        <span class="feature-label">{{ item.label }}</span>
        <span v-if="item.badge" class="feature-badge">{{ item.badge }}</span>
      </button>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useAuthStore } from '@/stores/auth';
import { Star, Medal, Check, Discount } from '@element-plus/icons-vue';

const router = useRouter();
const authStore = useAuthStore();

const featureItems = ref([
  { icon: Star, label: '猜你喜欢', path: '#recommend-section', badge: '', isAnchor: true },
  { icon: Medal, label: '会员中心', path: '/member-center', badge: '', isAnchor: false },
  { icon: Check, label: '签到有礼', path: '/sign', badge: '', isAnchor: false },
  { icon: Discount, label: '优惠券', path: '/coupons', badge: '', isAnchor: false },
]);

const iconColor = computed(() => {
  const level = authStore.memberLevelCode;
  if (level >= 3) {
    return '#B8860B';
  }
  if (level >= 2) {
    return '#757575';
  }
  return '#0F766E';
});

const handleClick = (item: { path: string; isAnchor: boolean }) => {
  if (item.isAnchor) {
    const element = document.querySelector(item.path);
    if (element) {
      const top = element.getBoundingClientRect().top + window.scrollY - 60;
      window.scrollTo({ top, behavior: 'smooth' });
    }
  } else {
    router.push(item.path);
  }
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.home-feature-cards {
  margin: 12px $app-page-gutter 0;
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.feature-card {
  position: relative;
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: flex-start;
  gap: 10px;
  padding: 16px 14px;
  border: 1px solid var(--ios-surface-border, rgba(0, 0, 0, 0.045));
  border-radius: var(--ios-surface-radius, 8px);
  background: var(--ios-surface-bg, #fff);
  box-shadow: var(--ios-surface-shadow);
  cursor: pointer;
  transition: transform $transition-fast;

  &:active {
    transform: scale(0.98);
  }
}

.feature-icon {
  transition: color 0.2s ease;
}

.feature-label {
  font-size: 12px;
  font-weight: 500;
  color: $color-text-title;
}

.feature-badge {
  position: absolute;
  top: 6px;
  right: 6px;
  padding: 1px 5px;
  border-radius: $radius-pill;
  background: $color-gold-soft;
  font-size: 10px;
  font-weight: 700;
  color: $color-price;
}
</style>
