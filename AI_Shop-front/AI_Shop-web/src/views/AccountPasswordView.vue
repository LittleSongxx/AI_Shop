<template>
  <div class="password-page">
    <div class="form-panel card">
      <section class="form-section">
        <p class="section-title">旧密码</p>
        <el-input v-model="pwd.oldPassword" type="password" show-password placeholder="请输入旧密码" size="large" />
      </section>

      <div class="section-divider" />

      <section class="form-section">
        <p class="section-title">新密码</p>
        <el-input v-model="pwd.password" type="password" show-password placeholder="8-18位，含字母与数字" size="large" />
        <p class="hint">{{ PASSWORD_FORMAT_HINT }}</p>
      </section>

      <section class="form-section">
        <p class="section-title">确认新密码</p>
        <el-input v-model="pwd.confirmPassword" type="password" show-password placeholder="请再次输入新密码" size="large" />
      </section>

      <p v-if="error" class="error-tip">{{ error }}</p>

      <div class="save-row">
        <el-button type="primary" round class="save-btn" :loading="submitting" @click="savePwd">确认修改</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { accountApi } from '@/api/modules';
import { useAuthStore } from '@/stores/auth';
import { isValidPassword, PASSWORD_FORMAT_HINT } from '@/constants/validation';

const router = useRouter();
const authStore = useAuthStore();
const pwd = reactive({ oldPassword: '', password: '', confirmPassword: '' });
const error = ref('');
const submitting = ref(false);

const savePwd = async () => {
  error.value = '';
  if (!pwd.oldPassword?.trim()) {
    ElMessage.warning('请输入旧密码');
    return;
  }
  if (!isValidPassword(pwd.password)) {
    error.value = PASSWORD_FORMAT_HINT;
    return;
  }
  if (pwd.password !== pwd.confirmPassword) {
    error.value = '两次新密码不一致';
    return;
  }
  submitting.value = true;
  try {
    await accountApi.updatePassword({ oldPassword: pwd.oldPassword, password: pwd.password });
    ElMessage.success('密码已修改，请重新登录');
    authStore.prepareLogoutNavigation();
    await authStore.logout(true);
    await router.replace({ path: '/login', query: {} });
  } catch (e: any) {
    error.value = e?.info || e?.message || '密码修改失败，请稍后重试';
  } finally {
    submitting.value = false;
  }
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.password-page {
  padding: 0 0 24px;
}

.form-panel {
  padding: 8px 16px 20px;
}

.form-section {
  padding: 12px 0;

  .section-title {
    margin: 0 0 10px;
    font-size: 13px;
    font-weight: 500;
    color: $color-text-muted;
  }

  .hint {
    margin: 8px 0 0;
    font-size: 12px;
    color: $color-text-muted;
  }
}

.section-divider {
  height: 1px;
  background: $color-border;
}

.error-tip {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: $color-error;
  text-align: center;
}

.save-row {
  display: flex;
  justify-content: center;
  padding: 20px 0 4px;

  .save-btn {
    min-width: 200px;
  }
}
</style>
