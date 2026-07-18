<template>
  <aside
    class="pc-cat-aside ignore"
    aria-label="商品分类"
    @mouseenter="cancelClose"
    @mouseleave="scheduleClose"
  >
    <div class="cat-head">
      <span class="cat-head-title">商品分类</span>
      <RouterLink to="/search" class="cat-head-more">全部分类</RouterLink>
    </div>
    <ul v-if="categories.length" class="cat-list">
      <li
        v-for="cat in categories"
        :key="cat.categoryId"
        class="cat-item"
        :class="{ active: activeId === cat.categoryId }"
        @mouseenter="setActive(cat.categoryId)"
      >
        <button type="button" class="cat-link" @click="goCategory(cat.categoryId)">
          <span class="cat-name">{{ cat.categoryName }}</span>
          <el-icon v-if="cat.children?.length" class="cat-arrow"><ArrowRight /></el-icon>
        </button>
        <div
          v-if="activeId === cat.categoryId && cat.children?.length"
          class="cat-flyout"
          @mouseenter="cancelClose"
        >
          <div class="flyout-inner">
            <h4 class="flyout-title">{{ cat.categoryName }}</h4>
            <div class="flyout-grid">
              <button
                v-for="sub in cat.children"
                :key="sub.categoryId"
                type="button"
                class="flyout-link"
                @click="goCategory(sub.categoryId)"
              >
                {{ sub.categoryName }}
              </button>
            </div>
            <button type="button" class="flyout-all" @click="goCategory(cat.categoryId)">
              查看全部
            </button>
          </div>
        </div>
      </li>
    </ul>
    <p v-else class="cat-empty">暂无分类</p>
  </aside>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { RouterLink, useRouter } from 'vue-router';
import { ArrowRight } from '@element-plus/icons-vue';

const props = defineProps<{
  categories: Array<{
    categoryId: string;
    categoryName: string;
    children?: Array<{ categoryId: string; categoryName: string }>;
  }>;
}>();

const router = useRouter();
const activeId = ref<string | null>(null);
let closeTimer: ReturnType<typeof setTimeout> | null = null;

const cancelClose = () => {
  if (closeTimer) {
    clearTimeout(closeTimer);
    closeTimer = null;
  }
};

const scheduleClose = () => {
  cancelClose();
  closeTimer = setTimeout(() => {
    activeId.value = null;
    closeTimer = null;
  }, 280);
};

const setActive = (id: string) => {
  cancelClose();
  const cat = props.categories.find((c) => c.categoryId === id);
  activeId.value = cat?.children?.length ? id : null;
};

const goCategory = (id: string) => {
  cancelClose();
  activeId.value = null;
  router.push(`/category/${id}`);
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-cat-aside.ignore {
  position: relative;
  width: 200px;
  flex-shrink: 0;
  background: $color-card;
  border: 1px solid $color-border-gray;
  border-radius: $radius-sm;
  min-height: 360px;
  overflow: visible;
  z-index: 5;

  .cat-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    border-bottom: 1px solid $color-border-gray;
  }

  .cat-head-title {
    font-size: 14px;
    font-weight: 600;
    color: $color-text-primary;
  }

  .cat-head-more {
    font-size: 12px;
    color: $color-text-muted;
    text-decoration: none;

    &:hover {
      color: $color-primary;
    }
  }

  .cat-list {
    list-style: none;
    margin: 0;
    padding: 4px 0;
  }

  .cat-item {
    position: relative;
  }

  .cat-item.active .cat-link {
    color: $color-primary;
    background: $color-cat-hover-bg;
  }

  .cat-link {
    width: 100%;
    height: 32px;
    padding: 0 12px;
    border: none;
    background: transparent;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    font-size: 14px;
    color: $color-text-primary;
    cursor: pointer;
    text-align: left;

    &:hover {
      color: $color-primary;
      background: $color-cat-hover-bg;
    }
  }

  .cat-name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 13px;
    line-height: 32px;
  }

  .cat-arrow {
    flex-shrink: 0;
    font-size: 12px;
    color: $color-text-muted;
  }

  .cat-empty {
    margin: 0;
    padding: 16px 12px;
    font-size: 13px;
    color: $color-text-muted;
  }

  .cat-flyout {
    position: absolute;
    left: 100%;
    top: 0;
    z-index: 30;
    padding-left: 8px;
    margin-left: -4px;

    &::before {
      content: '';
      position: absolute;
      left: 0;
      top: 0;
      width: 8px;
      height: 100%;
      min-height: 32px;
    }
  }

  .flyout-inner {
    width: 480px;
    max-height: 400px;
    overflow-y: auto;
    padding: 14px 16px;
    background: $color-card;
    border: 1px solid $color-border-gray;
    border-radius: $radius-sm;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  }

  .flyout-title {
    margin: 0 0 10px;
    font-size: 14px;
    font-weight: 600;
    color: $color-text-primary;
  }

  .flyout-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 20px;
  }

  .flyout-link {
    border: none;
    background: transparent;
    padding: 2px 0;
    font-size: 13px;
    line-height: 1.4;
    color: $color-text-body;
    cursor: pointer;
    white-space: nowrap;

    &:hover {
      color: $color-primary;
    }
  }

  .flyout-all {
    margin-top: 12px;
    border: none;
    background: transparent;
    color: $color-primary;
    font-size: 12px;
    cursor: pointer;
    padding: 0;
  }
}
</style>
