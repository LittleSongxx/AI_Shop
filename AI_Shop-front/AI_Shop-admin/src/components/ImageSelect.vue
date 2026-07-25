<template>
  <el-upload ref="uploaderRef" :multiple="false" :show-file-list="false" :http-request="selectImage"
    :accept="proxy.imageAccept">
    <div v-if="props.modelValue" class="cover">
      <Cover :source="props.modelValue" :width="width" :scale="scale"></Cover>
    </div>
    <div v-else class="iconfont icon-image image-upload"
      :style="{ width: width + 'px', height: width + 'px', 'font-size': width / 2 + 'px' }">
    </div>
  </el-upload>
</template>
<script setup>
import {
  ref,
  reactive,
  getCurrentInstance,
  nextTick,
  inject,
  computed,
} from 'vue'
const { proxy } = getCurrentInstance()
import { useRoute, useRouter } from 'vue-router'
const route = useRoute()
const router = useRouter()

import { uploadImage } from '@/utils/Api.js'

const props = defineProps({
  width: {
    type: Number,
    default: 100,
  },
  modelValue: {
    type: [String, File],
  },
  cutWidth: {
    type: Number,
    default: 150,
  },
  
  scale: {
    type: Number,
    default: 1,
  },
})
const emits = defineEmits(['update:modelValue'])
const selectImage = async (file) => {
  const result = await uploadImage(file.file, true)
  emits('update:modelValue', result)
}

</script>

<style lang="scss" scoped>
.image-upload {
  border: 1px dashed #d5dcfb;
  border-radius: 5px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #646464;
}

.cover {
  background: #f0f0f0;
  position: relative;
  overflow: hidden;
  border-radius: 5px;
}
</style>
