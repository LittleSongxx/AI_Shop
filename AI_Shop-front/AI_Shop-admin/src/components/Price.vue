<template>
  <div class="price">
    <div class="tag" :style="{ 'font-size': `${size * 0.8}px` }">￥</div>
    <div class="value" :style="{ 'font-size': `${size}px` }">{{ proxy.Utils.convert2Amount(price) }}</div>
  </div>
</template>

<script setup>
import { ref, reactive, getCurrentInstance, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
const { proxy } = getCurrentInstance()
const router = useRouter()
const route = useRoute()

const props = defineProps({
  price: {
    type: Number,
  },
  size: {
    type: Number,
    default: 14,
  },
})
</script>

<style lang="scss" scoped>
.price {
  display: flex;
  color: #ff0f23;
  font-weight: bold;
  align-items: flex-end;
}
</style>
