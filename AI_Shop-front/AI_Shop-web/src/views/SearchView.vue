<template>
  <div class="category-page" :class="{ 'is-simlect-cate': !isDesktop }">

    <template v-if="!isDesktop">
      <div class="productSort simlect-goods-cate">
        <header class="header header-fixed">
          <button type="button" class="input" @click="router.push('/search-portal')">
            <el-icon class="search-icon"><Search /></el-icon>
            <span class="placeholder">搜索商品名称</span>
          </button>
        </header>

        <el-skeleton :loading="loading" animated :rows="8" class="scroll-box-skeleton">
          <template #default>
            <div v-if="rootCategories.length" class="scroll-box">

              <div class="aside-text-overlay">
                <button
                  v-for="(root, index) in rootCategories"
                  :key="root.categoryId"
                  type="button"
                  class="item"
                  :class="{ on: navActive === index }"
                  @click="tapNav(index)"
                >
                  {{ root.categoryName }}
                </button>
              </div>

              <aside ref="asideRef" class="aside" :class="{ 'is-overflow': isAsideOverflow }" aria-label="一级分类" aria-hidden="true">
                <button
                  v-for="(root, index) in rootCategories"
                  :key="'spacer-' + root.categoryId"
                  type="button"
                  class="item item-spacer"
                  :class="{ on: navActive === index }"
                  tabindex="-1"
                >
                  {{ root.categoryName }}
                </button>
              </aside>

              <div ref="conterRef" class="conter" @scroll.passive="onConterScroll">
                <div class="conter-header-placeholder" />
                <section
                  v-for="(root, index) in rootCategories"
                  :id="sectionId(index)"
                  :key="root.categoryId"
                  class="listw"
                >
                  <div class="title">
                    <span class="line" />
                    <span class="name">{{ root.categoryName }}</span>
                    <span class="line" />
                  </div>
                  <div class="list">
                    <button type="button" class="item" @click="goCategory(root.categoryId)">
                      <span class="picture">
                        <span class="picture-fallback">{{ root.categoryName.slice(0, 1) }}</span>
                      </span>
                      <span class="name line1">全部商品</span>
                    </button>
                    <button
                      v-for="sub in root.children || []"
                      :key="sub.categoryId"
                      type="button"
                      class="item"
                      @click="goCategory(sub.categoryId)"
                    >
                      <span class="picture">
                        <span class="picture-fallback">{{ sub.categoryName.slice(0, 1) }}</span>
                      </span>
                      <span class="name line1">{{ sub.categoryName }}</span>
                    </button>
                  </div>
                </section>
              </div>
            </div>
            <el-empty v-else description="暂无分类数据" />
          </template>
        </el-skeleton>
      </div>
    </template>

    <div v-else class="card category-card">
      <div class="card-section-title">
        <h3>全部分类</h3>
        <span class="hint">共 {{ categoryCount }} 个分类</span>
      </div>

      <el-skeleton :loading="loading" animated :rows="6">
        <template #default>
          <div v-if="rootCategories.length" class="category-list">
            <section v-for="root in rootCategories" :key="root.categoryId" class="category-group">
              <button type="button" class="group-title" @click="goCategory(root.categoryId)">
                {{ root.categoryName }}
                <el-icon><ArrowRight /></el-icon>
              </button>
              <div v-if="root.children?.length" class="sub-grid">
                <button
                  v-for="sub in root.children"
                  :key="sub.categoryId"
                  type="button"
                  class="sub-item"
                  @click="goCategory(sub.categoryId)"
                >
                  {{ sub.categoryName }}
                </button>
              </div>
              <p v-else class="no-sub">点击查看该分类商品</p>
            </section>
          </div>
          <el-empty v-else description="暂无分类数据" />
        </template>
      </el-skeleton>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ArrowRight, Search } from '@element-plus/icons-vue';
import { productApi } from '@/api/modules';
import { countCategoryNodes, normalizeCategoryTree } from '@/utils/category';
import { useDevice } from '@/composables/useDevice';
import { usePageRefresh } from '@/composables/pullRefresh';

const router = useRouter();
const { isDesktop } = useDevice();
const loading = ref(true);
const rootCategories = ref<any[]>([]);
const navActive = ref(0);
const conterRef = ref<HTMLElement | null>(null);
const asideRef = ref<HTMLElement | null>(null);
const isAsideOverflow = ref(false);

const checkAsideOverflow = () => {
  const el = asideRef.value;
  if (!el) return;
  isAsideOverflow.value = el.scrollHeight > el.clientHeight;
};

const handleResize = () => {
  checkAsideOverflow();
};

const categoryCount = computed(() => countCategoryNodes(rootCategories.value));

const sectionId = (index: number) => `b${index}`;

const goCategory = (categoryId: string) => {
  router.push(`/category/${categoryId}`);
};

const tapNav = (index: number) => {
  navActive.value = index;
  const el = document.getElementById(sectionId(index));
  el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
};

const onConterScroll = () => {
  const root = conterRef.value;
  if (!root || !rootCategories.value.length) return;
  const top = root.scrollTop;
  let active = 0;
  for (let i = 0; i < rootCategories.value.length; i++) {
    const el = document.getElementById(sectionId(i));
    if (el && el.offsetTop - root.offsetTop <= top + 80) active = i;
  }
  navActive.value = active;
};

const load = async () => {
  loading.value = true;
  try {
    const data = await productApi.loadCategory();
    rootCategories.value = normalizeCategoryTree(data);
    nextTick(checkAsideOverflow);
  } finally {
    loading.value = false;
  }
};

onMounted(load);
onMounted(() => window.addEventListener('resize', handleResize));
usePageRefresh(load);
onUnmounted(() => window.removeEventListener('resize', handleResize));
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.category-page {
  padding-bottom: 8px;

  &.is-simlect-cate {
    padding-bottom: 0;
    height: 100%;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
}

.productSort.simlect-goods-cate {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
}

.productSort .header {
  flex-shrink: 0;
  padding: 8px $app-page-gutter;
  padding-top: calc(8px + env(safe-area-inset-top, 0));
  background: #fff;
  border-bottom: 1px solid #f5f5f5;

  &.header-fixed {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 100;
  }
}

.productSort .header .input {
  width: 100%;
  height: 34px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 14px;
  background: $color-bg-subtle;
  border: none;
  border-radius: 17px;
  cursor: pointer;
  text-align: left;

  .search-icon {
    color: $color-text-muted;
    flex-shrink: 0;
  }

  .placeholder {
    font-size: 13px;
    color: $color-text-muted;
  }
}

.scroll-box-skeleton {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.productSort .scroll-box {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

.productSort .aside-text-overlay {
  position: fixed;
  left: 0;
  top: calc(50px + env(safe-area-inset-top, 0));
  bottom: 0;
  width: 90px;
  z-index: 110;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  pointer-events: none;
  overscroll-behavior: contain;

  &::-webkit-scrollbar {
    display: none;
  }

  .item {
    display: block;
    width: 100%;
    min-height: 50px;
    padding: 14px 6px;
    border: none;
    background: transparent;
    font-size: 13px;
    color: #666666;
    text-align: center;
    cursor: pointer;
    line-height: 1.35;
    pointer-events: auto;

    &.on {
      background: rgba(255, 255, 255, 0.85);
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      border-left: 3px solid $color-primary;
      color: $color-primary;
      font-weight: 600;
      position: relative;
      z-index: 1;
    }
  }
}

.productSort .aside {
  flex: 0 0 90px;
  overflow-y: auto;
  background: #f7f7f7;
  -webkit-overflow-scrolling: touch;

  &::-webkit-scrollbar {
    display: none;
  }

  &.is-overflow::after {
    content: '';
    position: sticky;
    left: 0;
    bottom: 0;
    display: block;
    width: 100%;
    height: 40px;
    background: linear-gradient(to top, #f7f7f7, transparent);
    pointer-events: none;
  }
}

.productSort .aside .item-spacer {
  display: block;
  width: 100%;
  min-height: 50px;
  padding: 14px 6px;
  border: none;
  background: transparent;
  font-size: 13px;
  color: transparent;
  text-align: center;
  line-height: 1.35;
  pointer-events: none;
  user-select: none;

  &.on {
    border-left: 3px solid $color-primary;
    background: transparent;
    color: transparent;
  }
}

.productSort .conter {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 0 10px 24px;
  background: #fff;
  -webkit-overflow-scrolling: touch;
}

.conter-header-placeholder {
  height: calc(50px + env(safe-area-inset-top, 0));
}

.productSort .listw {
  padding-top: 12px;
}

.productSort .listw .title {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 44px;

  .line {
    flex: 1;
    max-width: 50px;
    height: 1px;
    background: #f0f0f0;
  }

  .name {
    margin: 0 12px;
    font-size: 14px;
    font-weight: 600;
    color: $color-text-title;
    white-space: nowrap;
  }
}

.productSort .list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 4px;
}

.productSort .list .item {
  width: calc(33.33% - 4px);
  margin-top: 8px;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.productSort .list .picture {
  width: 60px;
  height: 60px;
  border-radius: 50%;
  overflow: hidden;
  background: $color-bg-subtle;
  display: grid;
  place-items: center;
  border: 1px solid $color-border-light;
}

.productSort .picture-fallback {
  font-size: 18px;
  font-weight: 600;
  color: $color-text-body;
}

.productSort .list .name {
  margin-top: 6px;
  font-size: 12px;
  color: $color-text-body;
  max-width: 72px;
  text-align: center;
  line-height: 1.3;
}

.line1 {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.category-card {
  margin: 0;
  padding: 16px;
  border-radius: $radius-card;
  box-shadow: $shadow-card;
  border: 1px solid rgba(255, 255, 255, 0.85);
  background: $color-card;
}

.hint {
  font-size: 12px;
  color: $color-text-muted;
  font-weight: 400;
}

.category-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.category-group {
  padding-bottom: 14px;
  border-bottom: 1px solid $color-border;

  &:last-child {
    border-bottom: none;
    padding-bottom: 0;
  }
}

.group-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  margin: 0 0 10px;
  padding: 0;
  border: none;
  background: none;
  font-size: 15px;
  font-weight: 600;
  color: $color-text-title;
  cursor: pointer;
  text-align: left;

  &:hover {
    color: $color-primary;
  }
}

.sub-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}

.sub-item {
  padding: 10px 8px;
  border: none;
  border-radius: $radius-xs;
  background: $color-surface-inset;
  font-size: 12px;
  font-weight: 500;
  color: $color-text-body;
  cursor: pointer;
}

.no-sub {
  margin: 0;
  font-size: 12px;
  color: $color-text-muted;
}
</style>
