<template>
  <el-avatar :size="width" :src="avatarSrc" class="admin-avatar">
    <el-icon :size="iconSize"><UserFilled /></el-icon>
  </el-avatar>
</template>

<script setup>
import { computed } from 'vue'
import { UserFilled } from '@element-plus/icons-vue'
import { getCurrentInstance } from 'vue'
const { proxy } = getCurrentInstance()

const props = defineProps({
  avatar: {
    type: String,
    default: null,
  },
  width: {
    type: Number,
    default: 50,
  },
  lazy: {
    type: Boolean,
    default: true,
  },
})

const iconSize = computed(() => Math.floor(props.width * 0.45))

const avatarSrc = computed(() => {
  if (!props.avatar || props.avatar === 'avatar.png') {
    return ''
  }
  return proxy.Api.sourcePath + props.avatar
})
</script>

<style lang="scss" scoped>
.admin-avatar {
  background: linear-gradient(135deg, #f5f0e8, #e8e0d0);
  color: #8b7355;
  flex-shrink: 0;
}
</style>
