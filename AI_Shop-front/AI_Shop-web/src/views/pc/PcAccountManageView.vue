<template>
  <div class="pc-manage-page">
    <section class="user-brief">
      <UserAvatar :avatar="user.avatar" :size="56" />
      <div class="brief-text">
        <p class="nick">{{ user.nickName || '智选用户' }}</p>
        <p class="account">{{ user.email || '未绑定账号' }}</p>
      </div>
    </section>

    <section class="menu-card">
      <RouterLink to="/account/profile" class="menu-item">
        <el-icon class="menu-icon"><User /></el-icon>
        <span>个人资料</span>
        <el-icon class="arrow"><ArrowRight /></el-icon>
      </RouterLink>
      <div class="item-divider" />
      <RouterLink to="/account/settings" class="menu-item">
        <el-icon class="menu-icon"><Edit /></el-icon>
        <span>修改个人信息</span>
        <el-icon class="arrow"><ArrowRight /></el-icon>
      </RouterLink>
      <div class="item-divider" />
      <RouterLink to="/account/password" class="menu-item">
        <el-icon class="menu-icon"><Lock /></el-icon>
        <span>修改密码</span>
        <el-icon class="arrow"><ArrowRight /></el-icon>
      </RouterLink>
      <div class="item-divider" />
      <RouterLink to="/account/privacy" class="menu-item">
        <el-icon class="menu-icon"><DataAnalysis /></el-icon>
        <span>AI 数据与隐私</span>
        <el-icon class="arrow"><ArrowRight /></el-icon>
      </RouterLink>
    </section>

    <el-button class="btn-logout" plain type="danger" @click="logout">退出登录</el-button>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ArrowRight, DataAnalysis, Edit, Lock, User } from '@element-plus/icons-vue';
import UserAvatar from '@/components/common/UserAvatar.vue';
import { accountApi } from '@/api/modules';
import { useAuthStore } from '@/stores/auth';
import { confirmAction } from '@/utils/confirm';
import { toast } from '@/utils/toast';

const router = useRouter();
const authStore = useAuthStore();
const user = ref<Record<string, any>>({});

onMounted(async () => {
  user.value = (await accountApi.getUserInfo()) || authStore.userInfo || {};
});

const logout = async () => {
  const ok = await confirmAction('确定要退出当前账号吗？', {
    title: '退出登录',
    confirmButtonText: '退出'
  });
  if (!ok) return;
  authStore.prepareLogoutNavigation();
  await authStore.logout();
  toast.success('已退出登录');
  await router.replace({ path: '/login', query: {} });
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-manage-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 480px;
}

.user-brief {
  display: flex;
  align-items: center;
  gap: 14px;
  padding-bottom: 16px;
  border-bottom: 1px solid $color-border-gray;

  .nick {
    margin: 0 0 4px;
    font-size: 16px;
    font-weight: 600;
    color: $color-text-title;
  }

  .account {
    margin: 0;
    font-size: 13px;
    color: $color-text-muted;
  }
}

.menu-card {
  border: 1px solid $color-border-gray;
  border-radius: $radius-sm;
  overflow: hidden;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  font-size: 14px;
  color: $color-text-body;
  text-decoration: none;

  &:hover {
    background: #fafafa;
  }

  .menu-icon {
    color: $color-primary;
  }

  span {
    flex: 1;
  }

  .arrow {
    color: $color-text-muted;
  }
}

.item-divider {
  height: 1px;
  margin: 0 14px;
  background: $color-border;
}

.btn-logout {
  width: 100%;
  max-width: 280px;
  margin-top: 8px;
}
</style>
