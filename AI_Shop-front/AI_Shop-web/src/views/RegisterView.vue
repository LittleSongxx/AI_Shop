<template>
  <div class="auth-page ignore">
    <div class="auth-card">
      <div class="auth-brand">
        <BrandMark variant="light" class="brand-mark" />
        <div class="auth-brand-text">
          <h2>创建新账号</h2>
          <p class="brand-name">智选 · SmartSelect</p>
          <p class="brand-tip">注册智选，开启更聪明的购物</p>
        </div>
      </div>

      <el-form class="auth-form" label-position="top" @submit.prevent="submit">
        <el-form-item label="邮箱">
          <el-input v-model="form.email" placeholder="请输入邮箱" size="large" maxlength="150" />
        </el-form-item>
        <el-form-item label="昵称">
          <el-input v-model="form.nickName" placeholder="请输入昵称" size="large" maxlength="20" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.registerPassword"
            type="password"
            placeholder="8-18位，含字母与数字"
            size="large"
            show-password
          />
          <p class="pwd-hint">{{ PASSWORD_FORMAT_HINT }}</p>
          <div v-if="form.registerPassword" class="pwd-strength">
            <div class="pwd-strength-bar">
              <span :class="['seg', { on: passwordStrength >= 1, weak: passwordStrength === 1 }]" />
              <span :class="['seg', { on: passwordStrength >= 2, mid: passwordStrength === 2 }]" />
              <span :class="['seg', { on: passwordStrength >= 3, strong: passwordStrength === 3 }]" />
            </div>
            <span class="pwd-strength-text" :class="strengthClass">{{ strengthLabel }}</span>
          </div>
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input
            v-model="form.confirmPassword"
            type="password"
            placeholder="请再次输入密码"
            size="large"
            show-password
          />
        </el-form-item>
        <el-form-item label="邮箱验证码">
          <div class="email-code-row">
            <el-input
              v-model="form.emailCode"
              placeholder="请输入验证码"
              size="large"
              maxlength="6"
              class="code-input"
            />
            <el-button
              class="send-code-btn"
              size="large"
              :disabled="emailCodeCountdown > 0 || !form.email"
              @click="sendEmailCode"
            >
              {{ emailCodeCountdown > 0 ? `${emailCodeCountdown}s后重发` : '发送验证码' }}
            </el-button>
          </div>
        </el-form-item>
        <p v-if="error" class="error-tip">{{ error }}</p>
        <el-button type="primary" class="submit-btn" size="large" :loading="submitting" @click="submit">注 册</el-button>
      </el-form>

      <p class="auth-footer">
        已有账号？
        <RouterLink to="/login">去登录</RouterLink>
      </p>
    </div>
  </div>
  <SlideCaptchaDialog ref="slideCaptchaRef" />
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { RouterLink, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { toast } from '@/utils/toast';
import BrandMark from '@/components/common/BrandMark.vue';
import SlideCaptchaDialog from '@/components/business/SlideCaptchaDialog.vue';
import { accountApi } from '@/api/modules';
import { isValidEmail, isValidPassword, PASSWORD_FORMAT_HINT } from '@/constants/validation';
import { useEmailCode, type SlideCaptchaDialogExpose } from '@/composables/useEmailCode';

const router = useRouter();
const slideCaptchaRef = ref<SlideCaptchaDialogExpose | null>(null);
const error = ref('');
const submitting = ref(false);
const { emailCodeCountdown, startCountdown, requestSlideVerification } = useEmailCode();

const form = reactive({
  email: '',
  nickName: '',
  registerPassword: '',
  confirmPassword: '',
  emailCode: ''
});

const passwordStrength = computed(() => {
  const p = form.registerPassword;
  if (!p) return 0;
  let score = 0;
  if (p.length >= 6) score++;
  if (p.length >= 10) score++;
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) score++;
  if (/\d/.test(p)) score++;
  if (/[^A-Za-z0-9]/.test(p)) score++;
  if (score <= 1) return 1;
  if (score <= 3) return 2;
  return 3;
});

const strengthLabel = computed(() => {
  const map = ['', '弱', '中', '强'] as const;
  return map[passwordStrength.value] || '';
});

const strengthClass = computed(() => {
  const map = ['', 'weak', 'mid', 'strong'] as const;
  return map[passwordStrength.value] || '';
});

const sendEmailCode = async () => {
  error.value = '';
  if (!form.email?.trim()) {
    ElMessage.warning('请先输入邮箱');
    return;
  }
  if (!isValidEmail(form.email)) {
    error.value = '请输入有效的邮箱地址';
    return;
  }
  try {
    const captchaVerification = await requestSlideVerification(slideCaptchaRef.value);
    await accountApi.getEmailCode({ email: form.email, captchaVerification });
    ElMessage.success('验证码已发送');
    startCountdown();
  } catch (e: any) {
    error.value = e?.info || e?.message || '验证码发送失败';
  }
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
  if (!form.nickName?.trim()) {
    ElMessage.warning('请输入昵称');
    return;
  }
  if (!isValidPassword(form.registerPassword)) {
    error.value = PASSWORD_FORMAT_HINT;
    return;
  }
  if (form.registerPassword !== form.confirmPassword) {
    error.value = '两次密码不一致';
    return;
  }
  if (!form.emailCode) {
    error.value = '请输入邮箱验证码';
    return;
  }
  submitting.value = true;
  try {
    await accountApi.register({
      email: form.email,
      nickName: form.nickName,
      registerPassword: form.registerPassword,
      checkCode: form.emailCode
    });
    toast.success('注册成功，请登录');
    router.push('/login');
  } catch (e: any) {
    error.value = e?.info || e?.message || '注册失败，请稍后重试';
  } finally {
    submitting.value = false;
  }
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.auth-page.ignore {
  box-sizing: border-box;
  width: 100%;
  min-height: calc(100vh - 56px - 80px);
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 24px 16px 48px;
  background: $color-bg;
}

.auth-card {
  width: 100%;
  max-width: 420px;
  flex-shrink: 0;
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
    flex-shrink: 0;
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

.pwd-hint {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: $color-text-muted;
}

.pwd-strength {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  width: 100%;
}

.pwd-strength-bar {
  flex: 1;
  display: flex;
  gap: 4px;
  height: 6px;

  .seg {
    flex: 1;
    border-radius: $radius-xs;
    background: #eee;
    transition: background 0.2s;

    &.on.weak {
      background: rgba($color-primary, 0.35);
    }
    &.on.mid {
      background: rgba($color-primary, 0.6);
    }
    &.on.strong {
      background: $color-primary;
    }
  }
}

.pwd-strength-text {
  font-size: 12px;
  flex-shrink: 0;

  &.weak {
    color: $color-text-muted;
  }
  &.mid {
    color: $color-primary-hover;
  }
  &.strong {
    color: $color-primary;
  }
}

.submit-btn {
  width: 100%;
  margin-top: 4px;
  font-weight: 600;
  letter-spacing: 0;
}

.error-tip {
  margin: 0 0 12px;
  font-size: 13px;
  color: $color-price;
}

.email-code-row {
  display: flex;
  gap: 10px;

  .code-input {
    flex: 1;
  }

  .send-code-btn {
    flex-shrink: 0;
    white-space: nowrap;
  }
}

.auth-footer {
  margin: 0;
  padding: 16px 28px 28px;
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

@media (max-width: 480px) {
  .auth-page.ignore {
    padding: 16px 12px 32px;
    min-height: auto;
  }

  .auth-brand,
  .auth-form,
  .auth-footer {
    padding-left: 20px;
    padding-right: 20px;
  }
}
</style>
