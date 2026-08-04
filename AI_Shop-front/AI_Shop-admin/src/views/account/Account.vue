<template>
  <div class="login-page">
    <div class="login-panel">
      <section class="panel-brand" aria-hidden="true">
        <div class="brand-glow" />
        <div class="brand-content">
          <BrandMark variant="light" class="brand-mark" />
          <h1 class="brand-name">智选</h1>
          <p class="brand-tagline">SmartSelect · 运营后台</p>
          <p class="brand-desc">
            统一管理商品、订单、营销与数据，为日常运营提供清晰高效的工作台。
          </p>
          <ul class="brand-list">
            <li>商品与库存</li>
            <li>订单与物流</li>
            <li>营销与数据中心</li>
          </ul>
        </div>
      </section>

      <section class="panel-form">
        <div class="form-wrap">
          <header class="form-header">
            <h2>管理员登录</h2>
            <p>使用管理员账号进入系统</p>
          </header>

          <el-form
            class="login-form"
            :model="formData"
            :rules="rules"
            ref="formDataRef"
            label-position="top"
            @submit.prevent="doSubmit"
          >
            <el-form-item label="账号" prop="account">
              <el-input
                size="large"
                clearable
                placeholder="请输入账号"
                v-model="formData.account"
                maxlength="150"
              >
                <template #prefix>
                  <span class="iconfont icon-account" />
                </template>
              </el-input>
            </el-form-item>

            <el-form-item label="密码" prop="password">
              <el-input
                show-password
                size="large"
                placeholder="请输入密码"
                v-model="formData.password"
              >
                <template #prefix>
                  <span class="iconfont icon-password" />
                </template>
              </el-input>
            </el-form-item>

            <el-form-item label="验证码" prop="checkCode">
              <div class="check-code-row">
                <el-input
                  size="large"
                  placeholder="请输入验证码"
                  v-model="formData.checkCode"
                  @keyup.enter="doSubmit"
                >
                  <template #prefix>
                    <span class="iconfont icon-checkcode" />
                  </template>
                </el-input>
                <button
                  type="button"
                  class="captcha-btn"
                  title="点击刷新验证码"
                  @click="changeCheckCode"
                >
                  <img
                    v-if="checkCodeInfo.checkCode"
                    :src="checkCodeInfo.checkCode"
                    alt="验证码"
                    class="captcha-img"
                  />
                  <span v-else class="captcha-placeholder">加载中</span>
                </button>
              </div>
            </el-form-item>

            <el-button type="primary" size="large" class="submit-btn" @click="doSubmit">
              登录
            </el-button>
          </el-form>

          <p class="form-footer">
            <a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener">闽ICP备2026020850号</a>
          </p>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, getCurrentInstance } from 'vue'
import { useRouter } from 'vue-router'
import BrandMark from '@/components/BrandMark.vue'

const { proxy } = getCurrentInstance()
const router = useRouter()

const checkCodeInfo = ref({})
const changeCheckCode = async () => {
  const result = await proxy.Request({
    url: proxy.Api.checkCode,
  })
  if (!result) {
    return
  }
  checkCodeInfo.value = result.data
}
changeCheckCode()

const formData = ref({})
const formDataRef = ref()
const rules = {
  account: [{ required: true, message: '请输入账号' }],
  password: [{ required: true, message: '请输入密码' }],
  checkCode: [{ required: true, message: '请输入图片验证码' }],
}

const doSubmit = () => {
  formDataRef.value.validate(async (valid) => {
    if (!valid) {
      return
    }
    const params = { ...formData.value }
    params.checkCodeKey = checkCodeInfo.value.checkCodeKey
    const result = await proxy.Request({
      url: proxy.Api.login,
      params,
      errorCallback: () => {
        changeCheckCode()
      },
    })
    if (!result) {
      return
    }
    router.push('/home')
    proxy.Message.success('登录成功')
  })
}
</script>

<style lang="scss" scoped>
.login-page {
  --accent: #0f766e;
  --accent-hover: #0b665f;
  --accent-soft: #ecfdf5;
  --brand-ink: #ffffff;
  --brand-muted: rgba(255, 255, 255, 0.78);
  --ink: #17202a;
  --muted: #4b5b63;
  --line: #dde5e8;
  --panel: #ffffff;

  box-sizing: border-box;
  width: 100%;
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 24px;
  background-color: #f4f7f8;
}

.login-panel {
  box-sizing: border-box;
  display: grid;
  grid-template-columns: minmax(280px, 1fr) 400px;
  width: min(920px, 100%);
  min-height: 520px;
  border-radius: 8px;
  overflow: hidden;
  background: var(--panel);
  border: 1px solid rgba(255, 255, 255, 0.65);
  box-shadow:
    0 0 0 1px rgba(22, 22, 26, 0.04),
    0 20px 50px rgba(22, 22, 26, 0.1);
}

.panel-brand {
  position: relative;
  padding: 48px 40px;
  background: #0f766e;
  color: var(--brand-ink);
  overflow: hidden;

  .brand-glow {
    display: none;
  }

  .brand-content {
    position: relative;
    z-index: 1;
    max-width: 320px;
  }

  .brand-mark {
    width: 48px;
    height: 48px;
    margin-bottom: 24px;
    box-shadow: none;
  }

  .brand-name {
    margin: 0 0 6px;
    font-size: 28px;
    font-weight: 600;
    letter-spacing: 0;
    color: var(--brand-ink);
  }

  .brand-tagline {
    margin: 0 0 20px;
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0;
    text-transform: uppercase;
    color: var(--brand-muted);
  }

  .brand-desc {
    margin: 0 0 28px;
    font-size: 14px;
    line-height: 1.7;
    color: var(--brand-muted);
  }

  .brand-list {
    margin: 0;
    padding: 0;
    list-style: none;
    border-top: 1px solid rgba(74, 63, 58, 0.12);

    li {
      padding: 12px 0;
      font-size: 13px;
      color: var(--brand-muted);
      border-bottom: 1px solid rgba(74, 63, 58, 0.08);

      &::before {
        content: '—';
        margin-right: 10px;
        color: var(--accent-hover);
        opacity: 0.95;
      }
    }
  }
}

.panel-form {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 40px;
  background: #fafbfc;
}

.form-wrap {
  width: 100%;
  max-width: 320px;
}

.form-header {
  margin-bottom: 28px;

  h2 {
    margin: 0 0 8px;
    font-size: 22px;
    font-weight: 600;
    color: var(--ink);
    letter-spacing: 0;
  }

  p {
    margin: 0;
    font-size: 14px;
    color: var(--muted);
  }
}

.login-form {
  :deep(.el-form-item) {
    margin-bottom: 18px;
  }

  :deep(.el-form-item__label) {
    padding-bottom: 6px;
    font-size: 13px;
    font-weight: 500;
    color: #374151;
  }

  :deep(.el-input__wrapper) {
    border-radius: 8px;
    min-height: 42px;
    box-shadow: 0 0 0 1px var(--line) inset;
    background: #fff;
    transition: box-shadow 0.2s;
  }

  :deep(.el-input__wrapper.is-focus) {
    box-shadow: 0 0 0 1px var(--accent) inset;
  }

  :deep(.iconfont) {
    color: #9ca3af;
    font-size: 15px;
  }
}

.check-code-row {
  display: flex;
  gap: 10px;
  width: 100%;

  .el-input {
    flex: 1;
    min-width: 0;
  }
}

.captcha-btn {
  flex-shrink: 0;
  width: 108px;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
  overflow: hidden;
  transition: border-color 0.2s;

  &:hover {
    border-color: #c4c9d0;
  }
}

.captcha-img {
  display: block;
  width: 100%;
  height: 40px;
  object-fit: cover;
}

.captcha-placeholder {
  display: grid;
  place-items: center;
  height: 40px;
  font-size: 12px;
  color: var(--muted);
}

.submit-btn {
  width: 100%;
  margin-top: 4px;
  height: 44px;
  border-radius: 8px;
  font-size: 15px;
  font-weight: 500;
  letter-spacing: 0;
  border: none;
  --el-button-bg-color: var(--accent);
  --el-button-border-color: var(--accent);
  --el-button-hover-bg-color: var(--accent-hover);
  --el-button-hover-border-color: var(--accent-hover);
  --el-button-active-bg-color: var(--accent-hover);
  --el-button-active-border-color: var(--accent-hover);
}

.form-footer {
  margin: 24px 0 0;
  text-align: center;
  font-size: 12px;
  color: #b0b6c0;

  a {
    color: #b0b6c0;
    text-decoration: none;
    transition: color 0.2s;

    &:hover {
      color: var(--accent);
    }
  }
}

@media (max-width: 860px) {
  .login-page {
    padding: 16px;
    align-items: flex-start;
  }

  .login-panel {
    grid-template-columns: 1fr;
    width: 100%;
    max-width: 420px;
    min-height: auto;
  }

  .panel-brand {
    padding: 32px 28px 24px;

    .brand-desc,
    .brand-list {
      display: none;
    }

    .brand-mark {
      margin-bottom: 16px;
    }
  }

  .panel-form {
    padding: 32px 28px 36px;
  }
}
</style>
