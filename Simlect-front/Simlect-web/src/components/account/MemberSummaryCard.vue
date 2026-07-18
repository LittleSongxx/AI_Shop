<template>
  <RouterLink to="/member-center" class="member-summary" :class="cardLevelClass">
    <div class="member-summary__head">
      <div class="level-badge" :style="badgeStyle">{{ profile?.levelName || '普通会员' }}</div>
      <span class="level-lv" :style="lvStyle">Lv.{{ profile?.levelCode || 1 }}</span>
      <el-icon class="arrow"><ArrowRight /></el-icon>
    </div>
    <div class="growth-row">
      <span>成长值 {{ profile?.growthValue ?? 0 }}</span>
      <span class="hint">{{ growthHint }}</span>
    </div>
    <el-progress :percentage="growthPercent" :stroke-width="8" :show-text="false" :color="growthBarColor" />
    <p v-if="(claimableCount ?? 0) > 0" class="reward-tip">有 {{ claimableCount }} 项升级礼待领取</p>
  </RouterLink>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { RouterLink } from 'vue-router';
import { ArrowRight } from '@element-plus/icons-vue';
import { calcMemberGrowthHint, calcMemberGrowthPercent } from '@/constants/member';

const props = defineProps<{
  profile?: Record<string, any> | null;
  claimableCount?: number;

  nextLevelGrowth?: number | null;
  growthToNext?: number | null;
}>();

const growthHints = computed(() => ({
  nextLevelGrowth: props.nextLevelGrowth ?? null,
  growthToNext: props.growthToNext ?? null
}));
const growthPercent = computed(() =>
  calcMemberGrowthPercent(props.profile?.growthValue ?? 0, growthHints.value)
);
const growthHint = computed(() =>
  calcMemberGrowthHint(props.profile?.growthValue ?? 0, growthHints.value)
);
const growthBarColor = computed(() => {
  const code = Number(props.profile?.levelCode ?? 1);
  if (code >= 3) return '#c9a962';
  if (code >= 2) return '#a8a8ad';
  return '#c9a962';
});

const cardLevelClass = computed(() => {
  const code = Number(props.profile?.levelCode ?? 1);
  if (code >= 3) return 'level-gold';
  if (code >= 2) return 'level-silver';
  return 'level-default';
});

const badgeStyle = computed(() => {
  const code = Number(props.profile?.levelCode ?? 1);
  if (code >= 3) {
    return {
      background: 'linear-gradient(135deg, #e8c96a 0%, #c9a962 100%)',
      color: '#fff',
      border: '1px solid rgba(201, 169, 98, 0.45)'
    };
  }
  if (code >= 2) {
    return {
      background: 'linear-gradient(135deg, #d4d4d8 0%, #a8a8ad 100%)',
      color: '#fff',
      border: '1px solid rgba(168, 168, 173, 0.4)'
    };
  }
  return {
    background: 'var(--ios-fill-muted, #f5f5f7)',
    color: '#3c3c43',
    border: '1px solid var(--ios-separator, rgba(60, 60, 67, 0.1))'
  };
});

const lvStyle = computed(() => {
  const code = Number(props.profile?.levelCode ?? 1);
  if (code >= 3) {
    return { color: '#8b7355' };
  }
  if (code >= 2) {
    return { color: '#4a4a4f' };
  }
  return { color: '#333333' };
});
</script>

<style lang="scss">
@use '@/styles/variables' as *;

.member-summary {
  display: block;
  margin: 0 $app-page-gutter 8px;
  padding: 14px 16px;
  text-decoration: none;
  color: inherit;
  border-radius: var(--ios-surface-radius, 16px);
  -webkit-tap-highlight-color: transparent;
  position: relative;
  z-index: 1;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;

  &:active {
    opacity: 0.96;
  }
}

.member-summary.level-default {
  background: var(--ios-surface-bg, #fff);
  border: 1px solid var(--ios-surface-border, rgba(0, 0, 0, 0.045));
  box-shadow: var(--ios-surface-shadow);

  .level-lv {
    color: $color-text-title;
  }
}

.member-summary.level-silver {
  background: linear-gradient(165deg, #fafafa 0%, #f0f0f3 100%);
  border: 1px solid rgba(168, 168, 173, 0.32);
  box-shadow: var(--ios-surface-shadow);

  .level-lv {
    color: #636366;
  }
}

.member-summary.level-gold {
  background: linear-gradient(165deg, #fffcf5 0%, #faf6eb 100%);
  border: 1px solid rgba(201, 169, 98, 0.28);
  box-shadow: var(--ios-surface-shadow);

  .level-lv {
    color: #8b7355;
  }
}

.member-summary__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.level-badge {
  padding: 2px 10px;
  border-radius: $radius-pill;
  font-size: 12px;
  font-weight: 600;
}

.level-lv {
  font-size: 14px;
  font-weight: 600;
}

.arrow {
  margin-left: auto;
  color: $color-text-disabled;
}

.growth-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 8px;
  font-size: 13px;
  color: $color-text-body;

  .hint {
    font-size: 11px;
    color: $color-text-muted;
  }
}

.reward-tip {
  margin: 8px 0 0;
  font-size: 12px;
  color: $color-primary;
  font-weight: 500;
}

.member-summary.card {
  backdrop-filter: none !important;
  -webkit-backdrop-filter: none !important;
}
</style>
