<template>
  <div class="m-simple">
    <p class="m-note">在二级及以下分类上配置 SKU 属性（如颜色、规格）。点击属性可编辑，点「+ 属性」新增。</p>

    <div v-if="propertyCategories.length" class="m-list">
      <div v-for="node in propertyCategories" :key="node.categoryId" class="glass-card prop-card">
        <div class="prop-head">
          <span class="prop-path">{{ node.pathLabel }}</span>
          <button type="button" class="op-btn sm primary" @click="showEdit(node)">+ 属性</button>
        </div>
        <div v-if="node.productPropertyList?.length" class="prop-tags">
          <button
            v-for="tag in node.productPropertyList"
            :key="tag.propertyId"
            type="button"
            class="prop-tag"
            @click="showEdit(node, tag)"
          >
            {{ tag.propertyName }}<template v-if="tag.coverType == 1">(含图)</template>
            <span class="tag-del" @click.stop="delProperty(tag)">×</span>
          </button>
        </div>
        <p v-else class="prop-empty">暂无属性</p>
      </div>
    </div>
    <p v-else class="m-empty-tip">暂无可用分类</p>

    <ProductPropertyEdit ref="productPropertyEditRef" @reload="loadCategory" />
  </div>
</template>

<script setup>
import ProductPropertyEdit from '@/views/product/ProductPropertyEdit.vue'
import { ref, getCurrentInstance, onMounted } from 'vue'

const { proxy } = getCurrentInstance()
const categoryList = ref([])
const propertyCategories = ref([])
const productPropertyEditRef = ref(null)

const collectPropertyCategories = (nodes, parentPath = '') => {
  const result = []
  for (const n of nodes) {
    const path = parentPath ? `${parentPath} / ${n.categoryName}` : n.categoryName
    if (n.pCategoryId != '0' && n.pCategoryId !== 0) {
      result.push({ ...n, pathLabel: path })
    }
    if (n.children?.length) {
      result.push(...collectPropertyCategories(n.children, path))
    }
  }
  return result
}

const loadCategory = async () => {
  const result = await proxy.Request({
    url: proxy.Api.loadCategory,
    params: { queryProperty: true },
  })
  if (!result) return
  categoryList.value = result.data || []
  propertyCategories.value = collectPropertyCategories(categoryList.value)
}

const showEdit = ({ categoryId, pCategoryId }, tag) => {
  productPropertyEditRef.value.show({ categoryId, pCategoryId, ...tag })
}

const delProperty = (data) => {
  proxy.Confirm({
    message: `确定要删除【${data.propertyName}】吗?`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.delProductProperty,
        params: { propertyId: data.propertyId },
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
.prop-card {
  padding: 12px 14px;
}

.prop-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;

  .prop-path {
    flex: 1;
    min-width: 0;
    font-size: 14px;
    font-weight: 600;
    color: var(--m-ink);
    line-height: 1.4;
  }
}

.prop-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.prop-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  border: 1px solid rgba(120, 120, 128, 0.24);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.55);
  font-size: 13px;
  color: var(--m-ink-2);
  cursor: pointer;

  .tag-del {
    font-size: 16px;
    line-height: 1;
    color: var(--m-ink-3);
    padding-left: 2px;
  }
}

.prop-empty {
  margin: 0;
  font-size: 12px;
  color: var(--m-ink-3);
}
</style>
