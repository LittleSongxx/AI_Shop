<template>
  <div class="profile-page">
    <section class="hero card">
      <UserAvatar :avatar="user.avatar" :size="80" />
      <h2 class="nick">{{ user.nickName || '智选用户' }}</h2>
      <p class="sub">{{ user.email || '未绑定账号' }}</p>
    </section>

    <section class="detail-card card">
      <div class="info-row">
        <span class="label">昵称</span>
        <span class="value">{{ user.nickName || '—' }}</span>
      </div>
      <div class="row-divider" />
      <div class="info-row">
        <span class="label">账号</span>
        <span class="value">{{ user.email || '—' }}</span>
      </div>
      <div class="row-divider" />
      <div class="info-row">
        <span class="label">性别</span>
        <span class="value">{{ sexLabel }}</span>
      </div>
    </section>

    <p class="manage-hint">
      如需修改资料或退出登录，请前往
      <RouterLink to="/account/manage">设置</RouterLink>
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import UserAvatar from '@/components/common/UserAvatar.vue';
import { accountApi } from '@/api/modules';
import { useAuthStore } from '@/stores/auth';

const authStore = useAuthStore();
const user = ref<Record<string, any>>({});

const sexLabel = computed(() => {
  const map: Record<number, string> = { 0: '女', 1: '男', 2: '保密' };
  const s = user.value?.sex;
  return map[s as number] ?? '保密';
});

const load = async () => {
  user.value = (await accountApi.getUserInfo()) || authStore.userInfo || {};
};

onMounted(load);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.profile-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 24px;
}

.hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 28px 16px 24px;
  text-align: center;

  .nick {
    margin: 14px 0 6px;
    font-size: 20px;
    font-weight: 600;
    color: $color-text-title;
  }

  .sub {
    margin: 0;
    font-size: 13px;
    color: $color-text-muted;
  }
}

.detail-card {
  padding: 4px 16px;
}

.info-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 0;
  font-size: 14px;

  .label {
    color: $color-text-muted;
    flex-shrink: 0;
  }

  .value {
    color: $color-text-title;
    text-align: right;
    word-break: break-all;
  }
}

.row-divider {
  height: 1px;
  background: $color-border;
}

.manage-hint {
  margin: 4px 16px 0;
  text-align: center;
  font-size: 12px;
  color: $color-text-muted;

  a {
    color: $color-primary;
    text-decoration: none;
    font-weight: 500;
  }
}
</style>
