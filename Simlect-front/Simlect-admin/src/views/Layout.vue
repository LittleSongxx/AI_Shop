<template>
  <div class="layout">
    <aside class="left-side">
      <div class="left-side-content">
        <div class="logo">
          <div class="logo-row">
            <BrandMark class="logo-mark" />
            <div class="logo-copy">
              <span class="logo-text">简选</span>
              <span class="logo-sub">Simlect Admin</span>
            </div>
          </div>
        </div>
        <nav class="menu-nav">
          <template v-for="item in menuList" :key="item.path || item.name">
            <div :class="['menu-item', isMenuActive(item) ? 'active' : '']" @click="jump(item)">
              <div :class="['iconfont', `icon-${item.icon}`, 'menu-icon']"></div>
              <div class="menu-name">{{ item.name }}</div>
              <div
                v-if="item.children"
                :class="['iconfont', 'icon-right', 'icon-down', item.opened ? 'icon-right-opened' : 'icon-right-closed']"
              ></div>
            </div>
            <div
              v-if="item.children"
              :class="['submenu-container', item.opened ? 'submenu-opened' : 'submenu-closed']"
            >
              <div
                v-for="sub in item.children"
                :key="sub.path"
                :class="['submenu-item', route.path === sub.path ? 'active' : '']"
                @click="jump(sub)"
              >
                <span class="submenu-dot"></span>
                {{ sub.name }}
              </div>
            </div>
          </template>
        </nav>
      </div>
      <div class="sidebar-glow" aria-hidden="true"></div>
    </aside>

    <div class="right">
      <header class="top">
        <div class="top-main">
          <h1 class="page-title">{{ pageTitle }}</h1>
          <el-breadcrumb separator="/" class="breadcrumb">
            <el-breadcrumb-item v-for="item in route.meta.itemList" :key="item">
              {{ item }}
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="top-actions">
          <button type="button" class="action-pill" @click="tool">
            <span class="iconfont icon-setting action-pill__icon"></span>
            小工具
          </button>
          <button type="button" class="action-pill action-pill--ghost" @click="switchToMobile">
            手机版
          </button>
          <div class="user-chip">
            <span class="user-avatar">管</span>
            <span class="user-name">管理员</span>
            <button type="button" class="logout-btn" @click="logout">退出</button>
          </div>
        </div>
      </header>
      <main class="right-body" :class="{ 'is-home': route.path === '/home' }">
        <router-view></router-view>
      </main>
    </div>
  </div>
  <Tool ref="toolRef"></Tool>
</template>

<script setup>
import Tool from './Tool.vue'
import BrandMark from '@/components/BrandMark.vue'
import { ref, getCurrentInstance, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { switchToMobileView } from '@/utils/device'

const { proxy } = getCurrentInstance()
const router = useRouter()
const route = useRoute()

const pageTitle = computed(() => {
  const list = route.meta.itemList
  if (Array.isArray(list) && list.length) {
    return list[list.length - 1]
  }
  return '工作台'
})

const isMenuActive = (item) => {
  if (item.path && route.path === item.path) return true
  if (item.children) {
    return item.children.some((sub) => route.path === sub.path)
  }
  return false
}

const menuList = ref([
  {
    name: '首页',
    icon: 'home',
    path: '/home',
  },
  {
    name: '商品',
    icon: 'product',
    opened: true,
    children: [
      { name: '分类管理', path: '/product/category' },
      { name: '商品属性', path: '/product/ProductProperty' },
      { name: '商品管理', path: '/product' },
    ],
  },
  {
    name: '订单',
    icon: 'order',
    opened: true,
    children: [
      { name: '订单管理', path: '/order/orderList' },
      { name: '订单评论', path: '/order/comment' },
      { name: '举报管理', path: '/order/report' },
      { name: '图片违规复核', path: '/setting/imageModeration' },
    ],
  },
  {
    name: '用户管理',
    icon: 'user',
    opened: true,
    children: [
      { name: '用户列表', path: '/user/userList' },
      { name: '收货地址', path: '/user/address' },
    ],
  },
  {
    name: '数据中心',
    icon: 'home',
    opened: false,
    children: [
      { name: '统计明细', path: '/data/statistics' },
      { name: 'MQ补偿审查', path: '/data/mqCompensationLog' },
      { name: 'RAG同步失败', path: '/data/ragSyncFailure' },
      { name: '运营工具', path: '/data/tools' },
    ],
  },
  {
    name: '系统设置',
    icon: 'setting',
    opened: true,
    children: [
      { name: '发货信息管理', path: '/setting/logistics' },
      { name: '提示词管理', path: '/setting/prompt' },
      { name: 'RAG知识库', path: '/setting/rag' },
      { name: '敏感词管理', path: '/setting/sensitiveWord' },
      { name: '客服对话记录', path: '/setting/agentMessage' },
    ],
  },
  {
    name: '营销',
    icon: 'product',
    opened: true,
    children: [
      { name: '优惠券管理', path: '/discountCoupon' },
      { name: '热搜词管理', path: '/marketing/searchHot' },
      { name: '签到发券配置', path: '/marketing/signReward' },
      { name: '会员升级礼券', path: '/marketing/memberLevelReward' },
    ],
  },
])

const jump = (item) => {
  if (item.children) {
    item.opened = !item.opened
    return
  }
  router.push(item.path)
}

const logout = () => {
  proxy.Confirm({
    message: '确定要退出吗?',
    okfun: async () => {
      let result = await proxy.Request({
        url: proxy.Api.logout,
      })
      if (!result) {
        return
      }
      router.push('/login')
    },
  })
}

const toolRef = ref()
const tool = () => {
  toolRef.value.show()
}

const switchToMobile = () => {
  switchToMobileView('/m/home')
}
</script>

<style lang="scss" scoped>
.layout {
  display: flex;
  min-height: 100vh;
  background: transparent;

  .left-side {
    position: relative;
    flex-shrink: 0;
    width: 248px;
    height: 100vh;
    overflow: auto;
    background: linear-gradient(185deg, var(--sidebar-bg) 0%, var(--sidebar-bg-end) 100%);
    border-right: 1px solid rgba(255, 255, 255, 0.06);
    box-shadow: 4px 0 24px rgba(0, 0, 0, 0.08);

    .sidebar-glow {
      position: absolute;
      top: 0;
      right: 0;
      width: 120px;
      height: 120px;
      background: radial-gradient(circle, rgba(201, 117, 79, 0.18) 0%, transparent 70%);
      pointer-events: none;
    }

    .left-side-content {
      position: relative;
      z-index: 1;
      padding-bottom: 20px;

      .logo {
        padding: 22px 18px 18px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        margin-bottom: 10px;

        .logo-row {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .logo-mark {
          width: 36px;
          height: 40px;
          flex-shrink: 0;
        }

        .logo-copy {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }

        .logo-text {
          font-size: 17px;
          font-weight: 600;
          letter-spacing: 0.06em;
          color: var(--sidebar-text-active);
        }

        .logo-sub {
          font-size: 11px;
          font-weight: 500;
          color: var(--sidebar-text-muted);
          letter-spacing: 0.04em;
        }
      }

      .menu-item {
        display: flex;
        align-items: center;
        height: 44px;
        margin: 3px 12px;
        padding: 0 12px;
        border-radius: 12px;
        font-size: 14px;
        color: var(--sidebar-text);
        cursor: pointer;
        transition: background 0.2s, color 0.2s;

        &:hover {
          background: var(--sidebar-hover);
          color: var(--sidebar-text-active);
        }

        &.active {
          background: var(--sidebar-active-bg);
          color: var(--sidebar-text-active);
          font-weight: 600;
          box-shadow: inset 3px 0 0 var(--accent);
        }

        .menu-icon {
          font-size: 16px;
          opacity: 0.9;
        }

        .menu-name {
          flex: 1;
          margin-left: 10px;
          min-width: 0;
        }

        .icon-right {
          font-size: 11px;
          opacity: 0.65;
          transition: transform 0.25s ease;
        }

        .icon-right-opened {
          transform: rotate(180deg);
        }
      }

      .submenu-item {
        display: flex;
        align-items: center;
        gap: 8px;
        height: 38px;
        margin: 2px 14px 2px 22px;
        padding: 0 12px;
        border-radius: 10px;
        font-size: 13px;
        color: var(--sidebar-text-muted);
        cursor: pointer;
        transition: background 0.2s, color 0.2s;

        .submenu-dot {
          width: 5px;
          height: 5px;
          border-radius: 50%;
          background: rgba(255, 255, 255, 0.25);
          flex-shrink: 0;
        }

        &:hover {
          background: var(--sidebar-hover);
          color: var(--sidebar-text);
        }

        &.active {
          background: var(--sidebar-active-bg);
          color: var(--sidebar-text-active);
          font-weight: 500;

          .submenu-dot {
            background: var(--accent);
          }
        }
      }

      .submenu-container {
        transition: max-height 0.28s ease, opacity 0.22s ease;
      }

      .submenu-opened {
        max-height: 520px;
        opacity: 1;
      }

      .submenu-closed {
        max-height: 0;
        opacity: 0;
        overflow: hidden;
      }
    }
  }

  .right {
    flex: 1;
    width: 0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;

    .top {
      flex-shrink: 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      min-height: 64px;
      padding: 12px 24px;
      background: var(--header-bg);
      -webkit-backdrop-filter: blur(16px) saturate(160%);
      backdrop-filter: blur(16px) saturate(160%);
      border-bottom: 1px solid var(--header-border);
      box-shadow: var(--shadow-sm);

      .top-main {
        min-width: 0;

        .page-title {
          margin: 0 0 2px;
          font-size: 18px;
          font-weight: 600;
          color: var(--text);
          letter-spacing: 0.01em;
        }

        .breadcrumb {
          :deep(.el-breadcrumb) {
            line-height: 1.4;
            font-size: 12px;
          }

          :deep(.el-breadcrumb__inner) {
            color: var(--text3);
            font-weight: 400;
          }

          :deep(.el-breadcrumb__item:last-child .el-breadcrumb__inner) {
            color: var(--text2);
          }
        }
      }

      .top-actions {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-shrink: 0;
      }

      .action-pill {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        height: 36px;
        padding: 0 14px;
        border: 1px solid var(--header-border);
        border-radius: 999px;
        background: var(--surface);
        color: var(--text2);
        font-size: 13px;
        font-weight: 500;
        cursor: pointer;
        transition: border-color 0.2s, color 0.2s, box-shadow 0.2s;

        .action-pill__icon {
          font-size: 14px;
        }

        &:hover {
          color: var(--text);
          border-color: rgba(42, 36, 32, 0.14);
          box-shadow: var(--shadow-sm);
        }

        &.action-pill--ghost {
          background: transparent;
        }
      }

      .user-chip {
        display: flex;
        align-items: center;
        gap: 8px;
        height: 36px;
        padding: 0 6px 0 4px;
        border: 1px solid var(--header-border);
        border-radius: 999px;
        background: var(--surface);

        .user-avatar {
          width: 28px;
          height: 28px;
          border-radius: 50%;
          background: linear-gradient(135deg, var(--accent) 0%, #a86542 100%);
          color: #fff;
          font-size: 12px;
          font-weight: 600;
          display: flex;
          align-items: center;
          justify-content: center;
        }

        .user-name {
          font-size: 13px;
          color: var(--text2);
          padding-right: 4px;
        }

        .logout-btn {
          height: 28px;
          padding: 0 12px;
          border: none;
          border-radius: 999px;
          background: var(--primary-soft);
          color: var(--primary);
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          transition: background 0.2s;

          &:hover {
            background: var(--accent-soft);
            color: var(--accent-hover);
          }
        }
      }
    }

    .right-body {
      flex: 1;
      margin: 16px 16px 16px 0;
      padding: 20px 22px;
      border-radius: var(--card-radius);
      background: var(--surface);
      border: 1px solid var(--header-border);
      box-shadow: var(--shadow-card);
      overflow: auto;
      min-height: 0;

      &.is-home {
        background: transparent;
        border-color: transparent;
        box-shadow: none;
        padding: 8px 4px 20px 0;
      }
    }
  }
}
</style>
