<template>
  <header class="site-header ignore" :class="{ 'is-simlect-search': simlectSearch }">

    <div class="site-topbar">
      <div class="topbar-inner">
        <div class="topbar-left">
          <template v-if="authStore.isLoggedIn">
            <span class="topbar-text">欢迎您，{{ authStore.userInfo?.nickName || '用户' }}</span>
          </template>
          <template v-else>
            <RouterLink class="topbar-link" to="/login">你好，请登录</RouterLink>
            <RouterLink class="topbar-link" to="/register">免费注册</RouterLink>
          </template>
        </div>
        <nav class="topbar-right" aria-label="顶部快捷入口">
          <RouterLink v-if="authStore.isLoggedIn" class="topbar-link" to="/notifications">
            消息<span v-if="unreadCount" class="topbar-badge">({{ unreadCount > 99 ? '99+' : unreadCount }})</span>
          </RouterLink>
          <RouterLink v-if="authStore.isLoggedIn" class="topbar-link topbar-link--fold" to="/member-center">会员中心</RouterLink>
          <RouterLink class="topbar-link topbar-link--fold" to="/orders">我的订单</RouterLink>
          <RouterLink class="topbar-link topbar-link--fold" to="/cart">购物车</RouterLink>
          <RouterLink class="topbar-link topbar-link--fold" to="/wishlist">收藏夹</RouterLink>
          <RouterLink class="topbar-link topbar-link--fold" to="/coupons">优惠券</RouterLink>
          <button type="button" class="topbar-link topbar-link--fold" @click="openAgent()">智能客服</button>
        </nav>
      </div>
    </div>

    <div class="site-search-row">
      <div class="search-row-inner">
        <RouterLink class="brand" to="/">
          <BrandMark class="brand-icon" />
          <span class="brand-text">简选</span>
          <span class="brand-en">Simlect</span>
        </RouterLink>

        <div class="search-block">
          <div class="search-box">
            <el-select
              v-model="searchCategoryId"
              placeholder="分类"
              class="search-category"
              popper-class="search-category-popper"
            >
              <el-option label="全部商品" value="" />
              <el-option
                v-for="c in categoryList"
                :key="c.categoryId"
                :label="c.categoryName"
                :value="c.categoryId"
              />
            </el-select>
            <div class="search-divider" />
            <input
              v-model="keyword"
              class="search-input"
              type="search"
              placeholder="搜索简选"
              @focus="openRecentPanel"
              @blur="onSearchBlur"
              @keyup.enter="goSearch"
            />
            <button type="button" class="search-submit" @click="goSearch">搜索</button>
          </div>
          <div v-if="showRecentPanel && authStore.isLoggedIn" class="recent-panel card-flat">
            <div class="recent-head">
              <span>最近搜索</span>
              <button v-if="recentWords.length" type="button" class="clear-link" @click="clearRecent">
                清空最近搜索记录
              </button>
            </div>
            <div v-if="recentWords.length" class="recent-list">
              <div v-for="word in recentWords" :key="word" class="recent-row">
                <button type="button" class="recent-word" @click="searchByWord(word)">{{ word }}</button>
                <button type="button" class="recent-del" aria-label="删除" @click="removeRecent(word)">×</button>
              </div>
            </div>
            <p v-else class="recent-empty">暂无搜索记录</p>
          </div>
          <div v-if="hotWords.length" class="hot-words">
            <button
              v-for="(word, i) in hotWords"
              :key="`hot-${i}`"
              type="button"
              class="hot-word"
              @click="searchByWord(word)"
            >
              {{ word }}
            </button>
            <button type="button" class="hot-word hot-more" @click="router.push('/search-portal')">
              更多
            </button>
          </div>
        </div>

        <nav class="header-actions">
          <template v-if="!authStore.isLoggedIn">
            <RouterLink class="action-link" to="/login">请登录</RouterLink>
          </template>
          <el-dropdown v-else trigger="click" popper-class="user-dropdown-popper" :teleported="true">
            <button type="button" class="user-trigger">
              <el-avatar :size="36" :src="avatarUrl" class="user-avatar">
                {{ (authStore.userInfo?.nickName || '用')[0] }}
              </el-avatar>
              <span class="user-name">{{ authStore.userInfo?.nickName || '用户' }}</span>
              <el-icon><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="router.push('/orders')">我的订单</el-dropdown-item>
                <el-dropdown-item @click="router.push('/my-coupons')">我的优惠券</el-dropdown-item>
                <el-dropdown-item @click="router.push('/account')">个人中心</el-dropdown-item>
                <el-dropdown-item @click="router.push('/member-center')">会员中心</el-dropdown-item>
                <el-dropdown-item @click="router.push('/notifications')">
                  消息中心<span v-if="unreadCount" class="menu-badge">{{ unreadCount }}</span>
                </el-dropdown-item>
                <el-dropdown-item @click="router.push('/sign')">签到中心</el-dropdown-item>
                <el-dropdown-item divided @click="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <button type="button" class="icon-action" title="购物车" @click="router.push('/cart')">
            <el-badge :value="cartStore.cartCount" :hidden="!cartStore.cartCount" :max="99" :offset="[-2, 8]">
              <el-icon :size="26"><ShoppingCart /></el-icon>
            </el-badge>
            <span class="icon-label">购物车</span>
          </button>

          <button
            v-if="authStore.isLoggedIn"
            type="button"
            class="icon-action"
            title="消息"
            @click="router.push('/notifications')"
          >
            <el-badge :value="unreadCount" :hidden="!unreadCount" :max="99" :offset="[-2, 8]">
              <el-icon :size="26"><Bell /></el-icon>
            </el-badge>
            <span class="icon-label">消息</span>
          </button>
        </nav>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { RouterLink, useRouter } from 'vue-router';
import { ArrowDown, Bell, ShoppingCart } from '@element-plus/icons-vue';
import { useUnreadCount } from '@/composables/useUnreadCount';
import { productApi, searchApi } from '@/api/modules';
import { DEFAULT_HOT_SEARCH_WORDS } from '@/constants/searchHotWords';
import { confirmAction } from '@/utils/confirm';
import { useAuthStore } from '@/stores/auth';
import { useCartStore } from '@/stores/cart';
import { useSearchStore } from '@/stores/search';
import { flattenCategoryOptions, normalizeCategoryTree } from '@/utils/category';
import { resolveAvatarUrl } from '@/utils/image';
import { useOpenAgent } from '@/composables/useOpenAgent';
import BrandMark from '@/components/common/BrandMark.vue';
import { toast } from '@/utils/toast';

const props = withDefaults(
  defineProps<{
    simlectSearch?: boolean;
  }>(),
  { simlectSearch: false }
);

const router = useRouter();
const { openAgent } = useOpenAgent();
const searchStore = useSearchStore();
const authStore = useAuthStore();
const cartStore = useCartStore();
const { unreadCount } = useUnreadCount();

const keyword = ref('');
const searchCategoryId = ref('');
const categoryList = ref<any[]>([]);
const hotWords = ref<string[]>(DEFAULT_HOT_SEARCH_WORDS.slice(0, 8));
const recentWords = ref<string[]>([]);
const showRecentPanel = ref(false);

const avatarUrl = computed(() => resolveAvatarUrl(authStore.userInfo?.avatar));

const refreshRecent = async () => {
  if (!authStore.isLoggedIn) {
    recentWords.value = [];
    return;
  }
  try {
    const list = await searchApi.loadRecentKeywords();
    recentWords.value = Array.isArray(list) ? list.slice(0, 10) : [];
  } catch {
    recentWords.value = [];
  }
};

const openRecentPanel = async () => {
  showRecentPanel.value = true;
  await refreshRecent();
};

const closeRecentPanel = () => {
  showRecentPanel.value = false;
};

const onSearchBlur = () => {
  window.setTimeout(() => closeRecentPanel(), 180);
};

const goSearch = async () => {
  const keyWords = keyword.value.trim();
  if (!keyWords) {
    router.push('/search-portal');
    return;
  }
  if (authStore.isLoggedIn) {
    try {
      await searchApi.saveKeyword(keyWords);
    } catch {

    }
  }
  showRecentPanel.value = false;
  searchStore.setSearch({
    keyWords,
    categoryId: searchCategoryId.value || ''
  });
  router.push({ path: '/search-result', query: { q: keyWords } });
};

const searchByWord = (word: string) => {
  keyword.value = word;
  goSearch();
};

const clearRecent = async () => {
  const ok = await confirmAction('确定要清空全部最近搜索记录吗？', {
    title: '清空记录',
    confirmButtonText: '清空'
  });
  if (!ok) return;
  await searchApi.clearRecentKeywords();
  await refreshRecent();
};

const removeRecent = async (word: string) => {
  await searchApi.removeRecentKeyword(word);
  await refreshRecent();
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

onMounted(async () => {
  const cats = await productApi.loadCategory();
  categoryList.value = flattenCategoryOptions(normalizeCategoryTree(cats || []), 'child');
  if (authStore.isLoggedIn) cartStore.fetchCartCount();
  try {
    const hot = await searchApi.loadHotKeywords();
    if (Array.isArray(hot) && hot.length) hotWords.value = hot.slice(0, 8);
  } catch {

  }
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.site-header.ignore {
  position: sticky;
  top: 0;
  z-index: 1000;
  background: var(--glass-bg-header, #{$color-card});
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.06);

  .site-topbar {
    height: $pc-topbar-height;
    background: rgba(245, 245, 247, 0.55);
    border-bottom: none;
    font-size: 12px;
  }

  .topbar-inner {
    max-width: $content-width;
    height: 100%;
    margin: 0 auto;
    padding: 0 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .topbar-left {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    flex-wrap: wrap;
  }
  .topbar-right {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .topbar-text {
    color: $color-text-body;
    font-size: 12px;
  }

  .topbar-link {
    color: $color-text-body;
    font-size: 12px;
    text-decoration: none;
    white-space: nowrap;
    transition: color $transition-fast;
    border: none;
    background: transparent;
    padding: 0;
    cursor: pointer;
    font-family: inherit;

    &:hover {
      color: $color-primary;
    }
  }

  .topbar-badge {
    color: $color-primary;
    font-weight: 600;
  }

  .menu-badge {
    margin-left: 6px;
    padding: 0 6px;
    border-radius: $radius-sm;
    background: $color-price;
    color: #fff;
    font-size: 11px;
    line-height: 18px;
  }

  .site-search-row {
    height: $pc-search-row-height;
    background: transparent;
  }

  .search-row-inner {
    max-width: $content-width;
    height: 100%;
    margin: 0 auto;
    padding: 0 16px;
    display: grid;
    grid-template-columns: auto minmax($pc-search-min-width, 620px) auto;
    align-items: center;
    column-gap: 16px;
    overflow: hidden;
  }

  .brand {
    grid-column: 1;
    justify-self: start;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
    text-decoration: none;

    .brand-icon {
      width: 38px;
      height: 38px;
    }

    .brand-text {
      font-size: 21px;
      font-weight: 600;
      color: $color-primary;
      letter-spacing: 0.12em;
    }

    .brand-en {
      font-size: 11px;
      font-weight: 500;
      color: $color-text-muted;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      margin-left: -4px;
    }
  }

  .search-block {
    grid-column: 2;
    width: 100%;
    min-width: $pc-search-min-width;
    max-width: 620px;
    justify-self: center;
    position: relative;
    flex-shrink: 0;
  }

  .recent-panel {
    position: absolute;
    left: 0;
    right: 0;
    top: calc(100% + 4px);
    z-index: 20;
    padding: 10px 12px;
    background: #fff;
    border: 1px solid $color-border-gray;
    border-radius: $radius-sm;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
  }

  .recent-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 13px;
    margin-bottom: 8px;
  }

  .clear-link {
    border: none;
    background: none;
    color: $color-text-secondary;
    cursor: pointer;
    font-size: 12px;
  }

  .recent-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 4px 0;
  }

  .recent-word {
    border: none;
    background: none;
    text-align: left;
    cursor: pointer;
    font-size: 14px;
    flex: 1;
  }

  .recent-del {
    border: none;
    background: none;
    color: $color-text-secondary;
    cursor: pointer;
    font-size: 18px;
    line-height: 1;
  }

  .recent-empty {
    margin: 0;
    font-size: 13px;
    color: $color-text-secondary;
  }

  .search-box {
    width: 100%;
    max-width: 620px;
    height: 40px;
    display: flex;
    align-items: stretch;
    border: 1px solid $color-border-gray;
    border-radius: $radius-search;
    overflow: hidden;
    background: #fff;
    transition: border-color $transition-fast, box-shadow $transition-fast;

    &:focus-within {
      border-color: $color-primary;
      box-shadow: 0 0 0 3px rgba(232, 104, 93, 0.06);
    }

    .search-category {
      position: relative;
      width: 100px;
      flex-shrink: 0;

      :deep(.el-select__wrapper) {
        height: 100%;
        box-shadow: none !important;
        border-radius: 0;
        background: transparent;
        justify-content: center;
        padding-left: 8px;
        padding-right: 22px;
      }

      :deep(.el-select__selection) {
        flex: 1;
        min-width: 0;
        justify-content: center;
        text-align: center;
      }

      :deep(.el-select__selected-item),
      :deep(.el-select__placeholder) {
        width: 100%;
        text-align: center;
        justify-content: center;
      }

      :deep(.el-select__suffix) {
        position: absolute;
        right: 6px;
      }
    }

    .search-divider {
      width: 1px;
      align-self: center;
      height: 20px;
      background: rgba(0, 0, 0, 0.08);
    }

    .search-input {
      flex: 1;
      min-width: 0;
      border: none;
      outline: none;
      padding: 0 12px;
      font-size: 14px;
      color: $color-text-primary;
    }

    .search-submit {
      flex-shrink: 0;
      min-width: 72px;
      padding: 0 20px;
      border: none;
      border-left: none;
      background: $color-primary;
      color: #fff;
      font-size: 16px;
      font-weight: 600;
      cursor: pointer;
      transition: background $transition-fast, box-shadow $transition-fast, transform 0.15s cubic-bezier(0.34, 1.56, 0.64, 1), filter $transition-fast;

      &:hover {
        background: $color-primary-hover;
        box-shadow: 0 4px 16px rgba(232, 104, 93, 0.25);
        transform: translateY(-1px);
        filter: brightness(1.03);
      }

      &:active {
        transform: scale(0.94);
        background: $color-primary-active;
        box-shadow: 0 2px 8px rgba(232, 104, 93, 0.18);
        filter: brightness(0.92);
      }
    }
  }

  .hot-words {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: center;
    gap: 4px 12px;
    margin-top: 6px;
    padding-left: 0;
    width: 100%;
  }

  .hot-label {
    font-size: 12px;
    color: $color-text-muted;
  }

  .hot-word {
    border: none;
    background: transparent;
    padding: 0;
    font-size: 12px;
    color: $color-text-body;
    cursor: pointer;

    &:hover {
      color: $color-primary;
      text-decoration: underline;
    }
  }

  .header-actions {
    grid-column: 3;
    justify-self: end;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 12px;

    .action-link {
      font-size: 13px;
      color: $color-text-body;
      text-decoration: none;

      &:hover {
        color: $color-primary;
      }
    }

    .user-trigger {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 4px 8px 4px 4px;
      border: 1px solid $color-border-gray;
      border-radius: 20px;
      background: $color-card;
      cursor: pointer;
      transition: border-color $transition-fast, box-shadow $transition-fast;

      &:hover {
        border-color: rgba($color-primary, 0.3);
        box-shadow: 0 2px 8px rgba(232, 104, 93, 0.08);
      }

      .user-name {
        max-width: 80px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: 13px;
        color: $color-text-primary;
      }
    }

    .icon-action {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 2px;
      min-width: 56px;
      padding: 4px;
      border: none;
      background: transparent;
      color: $color-text-primary;
      cursor: pointer;
      border-radius: $radius-xs;
      transition: background $transition-fast, color $transition-fast;

      .icon-label {
        font-size: 12px;
        color: $color-text-body;
        transition: color $transition-fast;
      }

      &:hover {
        color: $color-primary;
        background: $color-primary-soft;

        .icon-label {
          color: $color-primary;
        }
      }
    }
  }
}
</style>
