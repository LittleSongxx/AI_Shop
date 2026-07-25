<template>
  <div class="member-center-page">
    <section v-if="center?.profile" class="member-card" :class="cardLevelClass" data-member-card>
      <p class="level-tag">{{ center.profile.levelName || '普通会员' }}</p>
      <h2 class="level-code">Lv.{{ center.profile.levelCode || 1 }}</h2>
      <div class="growth-row">
        <span>成长值</span>
        <strong>{{ center.profile.growthValue ?? 0 }}</strong>
      </div>
      <el-progress
        :percentage="growthPercent"
        :stroke-width="10"
        :show-text="false"
        :color="growthBarColor"
      />
      <p class="growth-hint">{{ growthHint }}</p>
    </section>

    <section v-if="center?.rewards?.length" class="rewards card-flat">
      <h3>等级奖励</h3>
      <ul class="reward-list">
        <li v-for="item in center.rewards" :key="item.levelCode" class="reward-item">
          <div class="reward-main">
            <p class="reward-level">{{ item.levelName }}</p>
            <p class="reward-title">{{ item.rewardTitle }}</p>
            <p class="reward-desc">{{ item.rewardDesc }}</p>
            <p class="reward-threshold">需成长值 ≥ {{ item.growthThreshold }}</p>
          </div>
          <div class="reward-action">
            <span v-if="item.claimed" class="status done">已领取</span>
            <span v-else-if="!item.unlocked" class="status lock">未达标</span>
            <el-button
              v-else-if="item.claimable"
              type="primary"
              size="small"
              :loading="claiming === item.levelCode"
              @click="onClaim(item.levelCode)"
            >
              领取
            </el-button>
            <span v-else class="status auto">已享有</span>
          </div>
        </li>
      </ul>
    </section>

    <section class="rules card-flat">
      <h3>成长规则</h3>
      <ul>
        <li>每消费 100 元获得 1 成长值（至少 1 点）</li>
        <li>每日签到 +5 成长值</li>
        <li>确认收货 +10 成长值</li>
        <li>1000 成长值升级银卡，5000 成长值升级金卡</li>
      </ul>
      <RouterLink to="/sign" class="sign-link">去签到</RouterLink>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { RouterLink } from 'vue-router';
import { userMemberApi } from '@/api/modules';
import { useAuthStore } from '@/stores/auth';
import { calcMemberGrowthHint, calcMemberGrowthPercent } from '@/constants/member';
import { confirmAction } from '@/utils/confirm';
import { toast } from '@/utils/toast';

const authStore = useAuthStore();
const center = ref<any>(null);
const claiming = ref<number | null>(null);

const growthHints = computed(() => ({
  nextLevelGrowth: center.value?.nextLevelGrowth ?? null,
  growthToNext: center.value?.growthToNext ?? null
}));
const growthPercent = computed(() =>
  calcMemberGrowthPercent(center.value?.profile?.growthValue ?? 0, growthHints.value)
);
const growthHint = computed(() =>
  calcMemberGrowthHint(center.value?.profile?.growthValue ?? 0, growthHints.value)
);
const growthBarColor = computed(() => {
  const code = Number(center.value?.profile?.levelCode ?? 1);
  if (code >= 3) return '#c9a962';
  if (code >= 2) return '#a8a8ad';
  return '#c9a962';
});

const cardLevelClass = computed(() => {
  const code = Number(center.value?.profile?.levelCode ?? 1);
  if (code >= 3) return 'level-gold';
  if (code >= 2) return 'level-silver';
  return 'level-default';
});

const load = async () => {
  center.value = await authStore.loadMemberCenter();
};

const onClaim = async (levelCode: number) => {
  const reward = center.value?.rewards?.find((r: any) => r.levelCode === levelCode);
  if (!reward) return;
  const ok = await confirmAction(
    `领取「${reward.rewardTitle}」\n${reward.rewardDesc}\n确定要领取吗？`,
    {
      title: '领取升级礼',
      confirmButtonText: '确定领取'
    }
  );
  if (!ok) return;
  claiming.value = levelCode;
  try {
    await userMemberApi.claimLevelReward(levelCode);
    center.value = await authStore.loadMemberCenter(true);
    toast.success('领取成功');
  } catch (e: any) {
    toast.error(e?.info || e?.message || '领取失败');
  } finally {
    claiming.value = null;
  }
};

onMounted(load);
</script>

<style lang="scss">
@use '@/styles/variables' as *;

.member-center-page {
  padding: 16px;
  max-width: 720px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.member-center-page [data-member-card] {
  padding: 20px !important;
  text-align: center !important;
  transition: all 0.3s ease !important;
  border-radius: $radius-card !important;
  margin-bottom: 16px !important;
  position: relative !important;
  z-index: 1 !important;
  background: transparent !important;
  -webkit-backdrop-filter: none !important;
  backdrop-filter: none !important;
}

.member-center-page [data-member-card].level-default {
  background: linear-gradient(135deg, #f5f5f7 0%, #e0e0e0 100%) !important;
  border: 1px solid #d1d1d6 !important;
  box-shadow: none !important;
}

.member-center-page [data-member-card].level-silver {
  background: linear-gradient(135deg, #e8e8ed 0%, #c8c8cc 50%, #a8a8ad 100%) !important;
  border: 1px solid #a8a8ad !important;
  box-shadow: 0 4px 20px rgba(168, 168, 173, 0.3) !important;
}

.member-center-page [data-member-card].level-silver .level-tag,
.member-center-page [data-member-card].level-silver .level-code {
  color: #4a4a4f !important;
}

.member-center-page [data-member-card].level-gold {
  background: linear-gradient(135deg, #fff8e7 0%, #f5e6c8 50%, #e8d5a3 100%) !important;
  border: 1px solid #c9a962 !important;
  box-shadow: 0 4px 20px rgba(201, 169, 98, 0.4) !important;
}

.member-center-page [data-member-card].level-gold .level-tag,
.member-center-page [data-member-card].level-gold .level-code {
  color: #8b7355 !important;
}

.level-tag {
  margin: 0;
  color: $color-primary;
  font-size: 14px;
}

.level-code {
  margin: 8px 0 16px;
  font-size: 28px;
}

.growth-row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 14px;
}

.growth-hint {
  margin: 10px 0 0;
  font-size: 13px;
  color: $color-text-secondary;
}

.rewards {
  padding: 16px;

  h3 {
    margin: 0 0 12px;
    font-size: 16px;
  }
}

.reward-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.reward-item {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 14px 0;
  border-bottom: 1px solid $color-border;

  &:last-child {
    border-bottom: none;
    padding-bottom: 0;
  }
}

.reward-main {
  flex: 1;
  min-width: 0;
}

.reward-level {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 600;
  color: $color-text-title;
}

.reward-title {
  margin: 0 0 4px;
  font-size: 14px;
  color: $color-primary;
}

.reward-desc,
.reward-threshold {
  margin: 0;
  font-size: 12px;
  color: $color-text-secondary;
  line-height: 1.5;
}

.reward-action {
  flex-shrink: 0;
  padding-top: 4px;

  .status {
    font-size: 12px;
    white-space: nowrap;

    &.done {
      color: $color-text-muted;
    }

    &.lock {
      color: $color-text-disabled;
    }

    &.auto {
      color: $color-success;
    }
  }
}

.rules {
  padding: 16px;

  h3 {
    margin: 0 0 10px;
    font-size: 16px;
  }

  ul {
    margin: 0;
    padding-left: 18px;
    font-size: 14px;
    color: $color-text-secondary;
    line-height: 1.8;
  }
}

.sign-link {
  display: inline-block;
  margin-top: 12px;
  color: $color-primary;
  font-size: 14px;
}
</style>
