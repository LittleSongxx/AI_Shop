<template>
  <div class="m-simple">
    <button type="button" class="op-btn primary block" @click="showEdit({ pCategoryId: '0' })">新增一级分类</button>

    <div v-if="categoryList.length" class="m-list">
      <div v-for="cat in categoryList" :key="cat.categoryId" class="glass-card cat-card">
        <div class="cat-head">
          <span class="cat-name">{{ cat.categoryName }}</span>
          <div class="cat-ops">
            <button type="button" class="op-btn sm" @click="showEdit({ pCategoryId: cat.categoryId })">子分类</button>
            <button type="button" class="op-btn sm" @click="showEdit(cat)">编辑</button>
            <button type="button" class="op-btn sm danger" @click="delCategory(cat)">删除</button>
          </div>
        </div>
        <div v-if="cat.children?.length" class="cat-children">
          <div v-for="sub in cat.children" :key="sub.categoryId" class="sub-row">
            <span class="sub-name">{{ sub.categoryName }}</span>
            <div class="sub-ops">
              <button type="button" class="op-btn sm" @click="showEdit(sub)">编辑</button>
              <button type="button" class="op-btn sm danger" @click="delCategory(sub)">删除</button>
            </div>
          </div>
        </div>
      </div>
    </div>
    <p v-else class="m-empty-tip">暂无分类</p>

    <CategoryEdit ref="categoryEditRef" @reload="loadCategory" />
  </div>
</template>

<script setup>
import CategoryEdit from '@/views/product/CategoryEdit.vue'
import { ref, getCurrentInstance, onMounted } from 'vue'

const { proxy } = getCurrentInstance()
const categoryList = ref([])
const categoryEditRef = ref(null)

const normalizeCategoryTree = (nodes) => {
  if (!Array.isArray(nodes)) return []
  return nodes.map((node) => {
    const pCategoryId =
      node.pCategoryId === null || node.pCategoryId === undefined || node.pCategoryId === ''
        ? '0'
        : String(node.pCategoryId)
    return {
      ...node,
      pCategoryId,
      children: normalizeCategoryTree(node.children || []),
    }
  })
}

const loadCategory = async () => {
  const result = await proxy.Request({
    url: proxy.Api.loadCategory,
    params: { querySku: false },
  })
  if (!result) return
  categoryList.value = normalizeCategoryTree(result.data || [])
}

const showEdit = (data = {}) => {
  categoryEditRef.value.show(data)
}

const delCategory = (data) => {
  proxy.Confirm({
    message: `确定要删除【${data.categoryName}】吗?`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.delCategory,
        params: { categoryId: data.categoryId },
      })
      if (!result) return
      proxy.Message.success('已删除')
      loadCategory()
    },
  })
}

onMounted(loadCategory)
</script>

<style lang="scss" scoped>
.cat-card {
  padding: 12px 14px;
}

.cat-head {
  display: flex;
  align-items: center;
  gap: 8px;

  .cat-name {
    flex: 1;
    min-width: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--m-ink);
  }

  .cat-ops {
    display: flex;
    gap: 6px;
    flex-shrink: 0;
  }
}

.cat-children {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed rgba(120, 120, 128, 0.2);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sub-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.45);

  .sub-name {
    flex: 1;
    min-width: 0;
    font-size: 14px;
    color: var(--m-ink-2);
  }

  .sub-ops {
    display: flex;
    gap: 6px;
    flex-shrink: 0;
  }
}
</style>
