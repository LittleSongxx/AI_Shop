<template>
  <div class="auth-page ignore">
    <div class="auth-card">
      <div class="auth-brand">
        <BrandMark variant="light" class="brand-mark" />
        <div class="auth-brand-text">
          <h2>欢迎回来</h2>
          <p class="brand-name">智选 · SmartSelect</p>
          <p class="brand-tip">登录智选，享受更聪明的购物</p>
        </div>
      </div>
      <el-form class="auth-form" label-position="top" @submit.prevent="submit">
        <el-form-item label="邮箱">
          <el-input v-model="form.email" placeholder="请输入邮箱" size="large" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" placeholder="请输入密码" size="large" show-password />
        </el-form-item>
        <el-form-item label="图形验证码">
          <CaptchaInput v-model="form.checkCode" :captcha-image="captchaSrc" @refresh="loadCode" />
        </el-form-item>
        <p v-if="error" class="error-tip">{{ error }}</p>
        <el-button type="primary" class="submit-btn" size="large" :loading="submitting" @click="submit">登 录</el-button>
        <p class="forgot-link-text">
          <RouterLink to="/forgot-password">忘记密码？</RouterLink>
        </p>
      </el-form>
      <p class="auth-footer">
        还没有账号？
        <RouterLink to="/register">去注册</RouterLink>
      </p>
    </div>
    <AppFooter />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { formatCaptchaSrc } from '@/utils/captcha';
import { RouterLink, useRoute, useRouter } from 'vue-router';
import BrandMark from '@/components/common/BrandMark.vue';
import CaptchaInput from '@/components/common/CaptchaInput.vue';
import AppFooter from '@/components/layout/AppFooter.vue';
import { accountApi } from '@/api/modules';
import { useAuthStore } from '@/stores/auth';
import { formatLoginErrorMessage } from '@/utils/apiError';
import { isValidEmail } from '@/constants/validation';
import { resolveSafeRedirect } from '@/utils/navigation';
import { toast } from '@/utils/toast';

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const error = ref('');
const submitting = ref(false);
const captcha = reactive({ checkCode: '', checkCodeKey: '' });
const form = reactive<any>({ email: '', password: '', checkCode: '' });
const captchaSrc = computed(() => formatCaptchaSrc(captcha.checkCode));

const loadCode = async () => {
  const data = await accountApi.checkCode();
  captcha.checkCode = data?.checkCode || '';
  captcha.checkCodeKey = data?.checkCodeKey || '';
};

const submit = async () => {
  error.value = '';
  if (!form.email?.trim()) {
    ElMessage.warning('请输入邮箱');
    return;
  }
  if (!isValidEmail(form.email)) {
    error.value = '请输入有效的邮箱地址';
    return;
  }
  if (!form.password) {
    ElMessage.warning('请输入密码');
    return;
  }
  if (!form.checkCode) {
    ElMessage.warning('请输入验证码');
    return;
  }
  submitting.value = true;
  try {
    await authStore.login({ ...form, checkCodeKey: captcha.checkCodeKey });
    toast.success('登录成功');
    router.replace(resolveSafeRedirect(route.query.redirect));
  } catch (e: any) {
    error.value = formatLoginErrorMessage(e);
    await loadCode();
  } finally {
    submitting.value = false;
  }
};

onMounted(loadCode);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.auth-page.ignore {
  box-sizing: border-box;
  width: 100%;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 24px 16px 0;
  background: $color-bg;
}

.auth-card {
  width: 100%;
  max-width: 420px;
  flex-shrink: 0;
  margin-bottom: 24px;
  background: $color-card;
  border-radius: $radius-card;
  border: 1px solid $color-border-light;
  box-shadow: $shadow-card-hover;
  overflow: hidden;
  animation: card-in 0.4s cubic-bezier(0.34, 1.1, 0.64, 1);
}

@keyframes card-in {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.auth-brand {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 30px 28px 26px;
  background: linear-gradient(135deg, $color-primary 0%, $color-primary-hover 100%);
  position: relative;

  &::after {
    content: '';
    position: absolute;
    left: 28px;
    right: 28px;
    bottom: 0;
    height: 2px;
    background: linear-gradient(90deg, $color-gold, transparent 70%);
  }

  .brand-mark {
    width: 48px;
    height: 48px;
  }

  .auth-brand-text {
    min-width: 0;
  }

  h2 {
    margin: 0;
    font-size: 20px;
    color: #fff;
  }

  .brand-name {
    margin: 6px 0 2px;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0;
    color: $color-gold;
  }

  .brand-tip {
    margin: 0;
    font-size: 12px;
    color: rgba(255, 255, 255, 0.78);
  }
}

.auth-form {
  padding: 20px 28px 8px;

  :deep(.el-form-item) {
    margin-bottom: 18px;
  }

  :deep(.el-form-item__label) {
    font-weight: 500;
    color: $color-text-title;
    padding-bottom: 6px;
  }

  :deep(.el-input) {
    width: 100%;
  }
}

.error-tip {
  margin: 0 0 12px;
  font-size: 13px;
  color: $color-price;
}

.submit-btn {
  width: 100%;
  margin-top: 4px;
  font-weight: 600;
  letter-spacing: 0;
}

.forgot-link-text {
  margin: 10px 0 0;
  text-align: right;
  font-size: 13px;

  a {
    color: $color-text-muted;
    text-decoration: none;

    &:hover {
      color: $color-primary;
    }
  }
}

.auth-footer {
  margin: 0;
  padding: 16px 28px 24px;
  text-align: center;
  font-size: 13px;
  color: $color-text-muted;

  a {
    color: $color-primary;
    font-weight: 500;

    &:hover {
      color: $color-primary-hover;
    }
  }
}
</style>
