<template>
  <div class="pc-page-embed pc-profile-page">
    <section class="profile-hero">
      <UserAvatar :avatar="user.avatar" :size="72" />
      <div class="profile-hero__text">
        <h2 class="nick">{{ user.nickName || '简选用户' }}</h2>
        <p class="sub">{{ user.email || '未绑定账号' }}</p>
      </div>
    </section>

    <section class="profile-detail card">
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

onMounted(async () => {
  user.value = (await accountApi.getUserInfo()) || authStore.userInfo || {};
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-profile-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.profile-hero {
  display: flex;
  align-items: center;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid $color-border-gray;

  .nick {
    margin: 0 0 4px;
    font-size: 18px;
    font-weight: 600;
    color: $color-text-primary;
  }

  .sub {
    margin: 0;
    font-size: 13px;
    color: $color-text-muted;
  }
}

.profile-detail {
  padding: 4px 16px;
}

.info-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  font-size: 14px;

  .label {
    color: $color-text-muted;
  }

  .value {
    color: $color-text-title;
    text-align: right;
  }
}

.row-divider {
  height: 1px;
  background: $color-border;
}

.manage-hint {
  margin: 0;
  text-align: center;
  font-size: 12px;
  color: $color-text-muted;

  a {
    color: $color-primary;
    text-decoration: none;
  }
}
</style>
