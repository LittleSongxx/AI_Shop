<template>
  <el-tree class="category-tree" :expand-on-click-node="false" default-expand-all :data="categoryList"
    node-key="categoryId">
    <template #default="{ node, data }">
      <div class="tree-node">
        <div class="node-label">{{ data.categoryName }}</div>
        <div class="sku-name">
          <el-tag v-for="tag in data.productPropertyList" :key="tag.propertId" closable @close.stop="delProperty(tag)"
            @click="showEdit(data, tag)">
            {{ tag.propertyName }}<template v-if="tag.coverType == 1">(含图)</template>
          </el-tag>
        </div>
        <el-tooltip effect="dark" content="新增SKU属性" placement="top">
          <div class="iconfont icon-add" v-if="data.pCategoryId != '0'" @click="showEdit(data)"></div>
        </el-tooltip>
      </div>
    </template>
  </el-tree>
  <ProductPropertyEdit ref="productPropertyEditRef" @reload="loadCategory"></ProductPropertyEdit>
</template>

<script setup>
import ProductPropertyEdit from './ProductPropertyEdit.vue'
import { ref, reactive, getCurrentInstance, nextTick, onMounted } from 'vue'
const { proxy } = getCurrentInstance()

const categoryList = ref([])
const loadCategory = async () => {
  let result = await proxy.Request({
    url: proxy.Api.loadCategory,
    params: {
      queryProperty: true,
    },
  })
  if (!result) {
    return
  }
  categoryList.value = result.data
}

const productPropertyEditRef = ref(null)
const showEdit = ({ categoryId, pCategoryId }, tag) => {
  productPropertyEditRef.value.show({ categoryId, pCategoryId, ...tag })
}

const delProperty = (data) => {
  proxy.Confirm({
    message: `确定要删除【${data.propertyName}】吗?`,
    okfun: async () => {
      let result = await proxy.Request({
        url: proxy.Api.delProductProperty,
        params: {
          propertyId: data.propertyId,
        },
      })
      if (!result) {
        return
      }
      loadCategory()
    },
  })
}
onMounted(() => {
  loadCategory()
})
</script>

<style lang="scss" scoped>
.category-tree {
  width: 700px;
  overflow: auto;

  :deep(.el-tree-node__content) {
    padding: 20px 0px;
  }

  .tree-node {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;

    .node-label {
      width: 200px;
    }

    .sku-name {
      flex: 1;
      display: flex;

      :deep(.el-tag) {
        margin-right: 10px;
      }
    }

    .icon-add {
      cursor: pointer;
      border: 1px solid var(--header-border);
      background: var(--primary-soft);
      color: var(--text2);
      display: flex;
      height: 22px;
      width: 22px;
      font-size: 14px;
      align-items: center;
      justify-content: center;
      border-radius: 5px;
      margin: 0px 10px;
      transition: all 0.2s ease;

      &:hover {
        border-color: var(--gold-border);
        background: var(--gold-soft);
        color: var(--primary);
      }
    }
  }
}
</style>
