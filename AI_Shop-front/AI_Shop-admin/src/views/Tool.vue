<template>
  <Dialog :show="dialogConfig.show" :title="dialogConfig.title" :buttons="dialogConfig.buttons" width="440px"
    :showCancel="false" @close="dialogConfig.show = false">
    <div class="tool-buttons">
      <el-button @click="buttonClick(1)" type="primary">同步统计数据</el-button>
      <el-button @click="buttonClick(2)" type="primary">同步商品数据</el-button>
      <el-button @click="buttonClick(3)" type="primary">同步RAG数据</el-button>
      <el-button @click="buttonClick(4)" type="primary">加入延时队列</el-button>
    </div>
  </Dialog>
</template>

<script setup>
import { ref, reactive, getCurrentInstance, nextTick } from "vue"
const { proxy } = getCurrentInstance();
const dialogConfig = ref({
  show: false,
  title: "小工具",
});

const show = () => {
  dialogConfig.value.show = true;
}
defineExpose({
  show
})

const API_MAP = {
  1: "toolStatistics",
  2: "toolProductData",
  3: "toolRagData",
  4: "toolAddAllOrderToDelayQueue",
}
const buttonClick = async (type) => {
  let result = await proxy.Request({
    url: proxy.Api[API_MAP[type]],
  })
  if (!result) {
    return;
  }
  proxy.Message.success("操作成功");
};
</script>

<style lang="scss" scoped>
.tool-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;

  .el-button {
    flex: 1;
    min-width: calc(50% - 5px);
  }
}
</style>
