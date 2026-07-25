<template>
  <div class="m-notice">
    <div class="glass-card notice-card">
      <span class="iconfont icon-edit notice-icon"></span>
      <h3 class="notice-title">商品编辑建议在电脑版</h3>
      <p class="notice-text">
        商品的发布与编辑涉及多规格 SKU、富文本详情与多图上传，移动端操作体验有限。
        点击下方按钮可一键切换到电脑版进行编辑。
      </p>
      <button type="button" class="notice-btn" @click="toDesktop">切换到电脑版编辑</button>
      <button type="button" class="notice-back" @click="back">返回商品列表</button>
    </div>
  </div>
</template>

<script setup>
import { getCurrentInstance } from 'vue'
import { useRouter } from 'vue-router'
import { resolveDesktopPath, switchToDesktopView } from '@/utils/device'

const { proxy } = getCurrentInstance()
const router = useRouter()

const toDesktop = () => {
  const desktopPath = resolveDesktopPath(router.currentRoute.value.path)
  proxy.Confirm({
    message: '切换到电脑版将使用桌面布局进行商品编辑，确定继续吗？',
    okfun: () => switchToDesktopView(desktopPath),
  })
}

const back = () => router.replace('/m/product')
</script>

<style lang="scss" scoped>
.m-notice {
  padding-top: 24px;
}

.notice-card {
  padding: 28px 20px;
  text-align: center;
}

.notice-icon {
  font-size: 40px;
  color: var(--m-gold);
}

.notice-title {
  margin: 14px 0 8px;
  font-size: 17px;
  font-weight: 600;
  color: var(--m-ink);
}

.notice-text {
  margin: 0 0 20px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--m-ink-2);
}

.notice-btn {
  width: 100%;
  height: 44px;
  margin-bottom: 10px;
  border: none;
  border-radius: 14px;
  background: var(--m-ink);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
}

.notice-back {
  width: 100%;
  height: 42px;
  border: 1px solid rgba(120, 120, 128, 0.24);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.5);
  color: var(--m-ink-2);
  font-size: 14px;
  cursor: pointer;
}
</style>
