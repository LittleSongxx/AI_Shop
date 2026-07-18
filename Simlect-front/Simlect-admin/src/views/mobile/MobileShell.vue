<template>
  <div class="admin-mobile-root">

    <header class="m-topbar glass-card glass-strong">
      <button v-if="showBack" type="button" class="m-top-btn" aria-label="返回" @click="goBack">
        <span class="iconfont icon-down back-icon"></span>
      </button>
      <div class="m-top-title">
        <span class="m-title-text">{{ title }}</span>
      </div>
      <button type="button" class="m-top-btn m-desktop-btn" title="切换到电脑版" @click="switchToDesktop">
        电脑版
      </button>
      <button type="button" class="m-top-btn" aria-label="退出" @click="logout">
        <span class="iconfont icon-setting"></span>
      </button>
    </header>

    <main class="m-content">
      <router-view v-slot="{ Component }">
        <transition name="m-fade" mode="out-in">
          <keep-alive :max="10">
            <component :is="Component" />
          </keep-alive>
        </transition>
      </router-view>
    </main>

    <Teleport to="body">
    <div class="m-tabbar-host" :class="{ 'is-jelly': barJelly }">
    <LiquidGlassSurface tag="nav" intensity="strong" class="m-tabbar" aria-label="底部导航">
      <div
        class="m-tabbar-inner"
        :style="{ '--tab-count': tabs.length, '--active-index': activeTabIndex }"
      >
        <LiquidGlassSurface
          intensity="strong"
          variant="active"
          class="m-tab-active-glass"
          :class="{ 'is-jelly': glassJelly }"
          aria-hidden="true"
        />
        <button
          v-for="tab in tabs"
          :key="tab.path"
          type="button"
          class="m-tab"
          :class="{ active: isTabActive(tab) }"
          @click="onTabClick(tab)"
        >
          <span class="iconfont m-tab-icon" :class="`icon-${tab.icon}`"></span>
          <span class="m-tab-label">{{ tab.label }}</span>
        </button>
      </div>
    </LiquidGlassSurface>
    </div>
    </Teleport>
  </div>
</template>

<script setup>
import { computed, getCurrentInstance } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { resolveDesktopPath, switchToDesktopView } from '@/utils/device'
import LiquidGlassSurface from '@/components/LiquidGlassSurface.vue'
import { useTabBarJelly } from '@/composables/useTabBarJelly.js'

const { proxy } = getCurrentInstance()
const router = useRouter()
const route = useRoute()

const tabs = [
  { label: '工作台', path: '/m/home', icon: 'home' },
  { label: '商品', path: '/m/product', icon: 'product' },
  { label: '订单', path: '/m/order', icon: 'order' },
  { label: '用户', path: '/m/user', icon: 'user' },
  { label: '更多', path: '/m/more', icon: 'setting' }
]

const tabPaths = tabs.map((t) => t.path)
const title = computed(() => route.meta?.title || '简选运营')
const showBack = computed(() => !tabPaths.includes(route.path) && !!route.meta?.showBack)

const isTabActive = (tab) => {
  if (route.path === tab.path) return true

  return route.meta?.tab === tab.path
}

const activeTabIndex = computed(() => {
  const idx = tabs.findIndex((tab) => isTabActive(tab))
  return idx >= 0 ? idx : 0
})

const { barJelly, glassJelly, onTabPress } = useTabBarJelly(activeTabIndex)

const onTabClick = (tab) => {
  onTabPress()
  goTab(tab.path)
}

const goTab = (path) => {
  if (route.path !== path) router.push(path)
}

const goBack = () => {
  if (window.history.length > 1) router.back()
  else router.push('/m/home')
}

const logout = () => {
  proxy.Confirm({
    message: '确定要退出登录吗?',
    okfun: async () => {
      await proxy.Request({ url: proxy.Api.logout })
      router.push('/login')
    }
  })
}

const switchToDesktop = () => {
  const desktopPath = resolveDesktopPath(route.path)
  proxy.Confirm({
    message: '切换到电脑版将使用桌面布局，在当前设备上可能需要横屏或左右滑动查看，确定继续吗？',
    okfun: () => switchToDesktopView(desktopPath),
  })
}
</script>

<style lang="scss" scoped>
@use '@/styles/tab-bar-jelly' as jelly;

.admin-mobile-root {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  min-height: 100dvh;
  overflow-x: hidden;
}

.m-topbar {
  position: sticky;
  top: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 52px;
  margin: 8px 10px 0;
  padding: 0 8px;
  border-radius: 18px;

  .m-top-title {
    flex: 1;
    min-width: 0;
    text-align: center;

    .m-title-text {
      font-size: 17px;
      font-weight: 600;
      letter-spacing: 0.02em;
      color: var(--m-ink);
    }
  }

  .m-top-btn {
    flex-shrink: 0;
    width: 38px;
    height: 38px;
    display: grid;
    place-items: center;
    border: none;
    border-radius: 12px;
    background: rgba(255, 255, 255, 0.5);
    color: var(--m-ink);
    cursor: pointer;
    transition: background 0.2s, transform 0.15s;

    &:active {
      transform: scale(0.92);
      background: var(--m-gold-soft);
    }

    .iconfont {
      font-size: 18px;
    }

    .back-icon {
      transform: rotate(90deg);
    }
  }

  .m-desktop-btn {
    width: auto;
    min-width: 52px;
    padding: 0 10px;
    font-size: 12px;
    font-weight: 600;
    color: var(--m-gold);
    background: var(--m-gold-soft);
  }
}

.m-content {
  flex: 1;
  min-height: 0;
  padding: 12px 10px calc(78px + env(safe-area-inset-bottom, 0));
  overflow-x: auto;

  :deep(.table-panel),
  :deep(.table-data-card),
  :deep(.top-panel),
  :deep(.form-style) {
    min-width: 0;
  }
}

.m-tabbar-host {
  position: fixed;
  left: 10px;
  right: 10px;
  bottom: calc(10px + env(safe-area-inset-bottom, 0));
  z-index: 60;
  width: auto;
  max-width: 480px;
  margin: 0 auto;
  pointer-events: none;
  @include jelly.tab-bar-jelly-host;
}

.m-tabbar-host .m-tabbar {
  pointer-events: auto;
}

.m-tabbar {
  position: relative;
  left: auto;
  right: auto;
  bottom: auto;
  transform: none;
  z-index: auto;
  width: 100%;
  max-width: none;
  padding: 0;
  border: none;
  border-radius: 999px;
  @include jelly.tab-bar-jelly-shell;
  box-shadow:
    0 6px 6px rgba(0, 0, 0, 0.18),
    0 0 20px rgba(0, 0, 0, 0.08);

  :deep(.liquid-glass-surface__content) {
    width: 100%;
  }
}

.m-tabbar-inner {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-around;
  width: 100%;
  height: 52px;
  padding: 4px 6px;
  overflow: visible;
}

.m-tab-active-glass {
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 6px;
  width: calc((100% - 12px) / var(--tab-count));
  border-radius: 999px;
  pointer-events: none;
  z-index: 0;
  transform: translateX(calc(var(--active-index) * 100%));
  box-shadow:
    0 2px 6px rgba(0, 0, 0, 0.12),
    0 0 12px rgba(0, 0, 0, 0.06);
  @include jelly.tab-bar-jelly-glass;
}

.m-tab {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  border: none;
  background: transparent;
  color: var(--m-ink-3);
  cursor: pointer;
  transition: color 0.25s ease;
  padding: 0 2px;
  @include jelly.tab-bar-jelly-items;

  .m-tab-icon {
    font-size: 20px;
    line-height: 1;
  }

  .m-tab-label {
    font-size: 10px;
    font-weight: 500;
    line-height: 1;
  }

  &:active {
    transform: none;
  }

  &.active {
    color: var(--m-gold);

    .m-tab-icon {
      color: var(--m-gold);
      transform: scale(1.06);
    }

    .m-tab-label {
      font-weight: 600;
    }
  }
}

.m-fade-enter-active,
.m-fade-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.m-fade-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.m-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
