<template>
  <div class="manage-page">
    <section class="user-brief card">
      <UserAvatar :avatar="user.avatar" :size="56" />
      <div class="brief-text">
        <p class="nick">{{ user.nickName || '简选用户' }}</p>
        <p class="account">{{ user.email || '未绑定账号' }}</p>
      </div>
    </section>

    <section class="menu-card card">
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
    </section>

    <div class="logout-row">
      <el-button class="btn-logout" plain type="danger" round @click="logout">退出登录</el-button>
    </div>

    <div class="footer-info">
      <a href="http://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer" class="beian-link">闽ICP备2026020850号</a>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ArrowRight, Edit, Lock, User } from '@element-plus/icons-vue';
import UserAvatar from '@/components/common/UserAvatar.vue';
import { accountApi } from '@/api/modules';
import { useAuthStore } from '@/stores/auth';
import { confirmAction } from '@/utils/confirm';
import { toast } from '@/utils/toast';

const router = useRouter();
const authStore = useAuthStore();
const user = ref<Record<string, any>>({});

const load = async () => {
  user.value = (await accountApi.getUserInfo()) || authStore.userInfo || {};
};

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

onMounted(load);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.manage-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 24px;
}

.user-brief {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;

  .nick {
    margin: 0 0 4px;
    font-size: 16px;
    font-weight: 600;
    color: $color-text-title;
  }

  .account {
    margin: 0;
    font-size: 12px;
    color: $color-text-muted;
  }
}

.menu-card {
  padding: 4px 0;
  overflow: hidden;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  text-decoration: none;
  color: $color-text-title;
  font-size: 14px;
  transition: background $transition-fast;

  &:hover {
    background: rgba($color-primary, 0.04);
    color: $color-primary;

    .menu-icon {
      color: $color-primary;
    }
  }

  span {
    flex: 1;
  }

  .menu-icon {
    color: $color-text-muted;
    font-size: 20px;
  }

  .arrow {
    color: $color-text-disabled;
    font-size: 14px;
  }
}

.item-divider {
  height: 1px;
  margin: 0 16px;
  background: $color-border;
}

.logout-row {
  display: flex;
  justify-content: center;
  padding: 8px 16px 0;

  .btn-logout {
    min-width: 200px;
  }
}

.footer-info {
  display: flex;
  justify-content: center;
  padding: 24px 16px;

  .beian-link {
    font-size: 12px;
    color: $color-text-muted;
    text-decoration: none;

    &:hover {
      color: $color-primary;
      text-decoration: underline;
    }
  }
}
</style>
