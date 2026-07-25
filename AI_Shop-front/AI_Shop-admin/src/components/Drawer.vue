<template>
  <el-drawer :size="width" :model-value="show" header-class="cust-drawer-header" body-class="cust-drawer-body"
    footer-class="cust-drawer-footer">
    <template #header>
      <div class="title">{{ title }}</div>
    </template>
    <template #default>
      <slot></slot>
    </template>
    <template #footer v-if="(buttons && buttons.length > 0) || showCancel">
      <el-button link @click="close" v-if="showCancel"> 取消 </el-button>
      <el-button v-for="btn in buttons" :type="btn.type || 'primary'" @click="btn.click">
        {{ btn.text }}
      </el-button>
    </template>
  </el-drawer>
</template>

<script setup>
const props = defineProps({
  title: {
    type: String,
  },
  show: {
    type: Boolean,
    default: false,
  },
  showCancel: {
    type: Boolean,
    default: true,
  },
  width: {
    type: String,
    default: '50%',
  },
  buttons: {
    type: Array,
  },
})

const emit = defineEmits()
const close = () => {
  emit('close')
}
</script>

<style lang="scss">
.cust-drawer-header {
  margin-bottom: 0px;
  padding: 5px 10px;

  .title {
    font-size: 16px;
    font-weight: bold;
  }
}

.cust-drawer-body {
  padding: 5px 15px 15px 15px;
}

.cust-drawer-footer {
  padding: 10px;
  text-align: center;
}
</style>