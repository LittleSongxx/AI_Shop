<template>
  <div
    class="coupon-order-cover"
    :style="{
      width: width ? `${width}px` : '100%',
      borderRadius,
      aspectRatio: scale,
    }"
  >
    <el-icon class="coupon-icon" :size="iconSize"><Ticket /></el-icon>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Ticket } from '@element-plus/icons-vue'

const props = defineProps({
  width: {
    type: Number,
    default: 80,
  },
  scale: {
    type: Number,
    default: 1,
  },
  borderRadius: {
    type: String,
    default: '5px',
  },
})

const iconSize = computed(() => {
  const w = props.width || 80
  if (w <= 52) return 26
  if (w <= 76) return 32
  return Math.round(w * 0.4)
})
</script>

<style lang="scss" scoped>
.coupon-order-cover {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  overflow: hidden;
  background: linear-gradient(135deg, rgba(29, 29, 31, 0.12), rgba(212, 162, 78, 0.1));

  .coupon-icon {
    color: rgba(29, 29, 31, 0.45);
  }
}
</style>
