<template>
  <div class="category-nav-strip" aria-label="商品分类">
    <div class="category-scroll">
      <button
        v-for="cat in displayCategories"
        :key="cat.categoryId"
        type="button"
        class="category-item"
        @click="goCategory(cat)"
      >
        {{ cat.categoryName }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { productApi } from '@/api/modules';
import { normalizeCategoryTree } from '@/utils/category';

const router = useRouter();
const categories = ref<any[]>([]);

const displayCategories = computed(() => {
  const flat: { categoryId: string; categoryName: string }[] = [];
  const walk = (nodes: any[]) => {
    for (const n of nodes) {
      flat.push({ categoryId: n.categoryId, categoryName: n.categoryName });
      if (flat.length >= 12) return;
      if (n.children?.length) walk(n.children);
      if (flat.length >= 12) return;
    }
  };
  walk(categories.value);
  return flat.slice(0, 12);
});

const goCategory = (cat: { categoryId: string }) => {
  router.push(`/category/${cat.categoryId}`);
};

onMounted(async () => {
  try {
    const cats = await productApi.loadCategory();
    categories.value = normalizeCategoryTree(cats || []);
  } catch {

  }
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.category-nav-strip {
  width: 100%;
  margin: 8px 0 0;
}

.category-scroll {
  display: flex;
  gap: 2px;
  overflow-x: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
  padding: 2px 0 0;

  &::-webkit-scrollbar {
    display: none;
  }

  .category-item:first-child {
    margin-left: $app-page-gutter;
  }

  &::after {
    content: '';
    flex-shrink: 0;
    width: $app-page-gutter;
  }
}

.category-item {
  flex: 0 0 auto;
  padding: 3px 10px;
  border: none;
  background: transparent;
  color: rgba(60, 60, 67, 0.82);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.2;
  letter-spacing: 0;
  cursor: pointer;
  border-radius: 8px;
  transition: background $transition-fast, color $transition-fast;

  &:active {
    background: rgba(0, 0, 0, 0.05);
    color: $color-text-title;
  }
}
</style>
