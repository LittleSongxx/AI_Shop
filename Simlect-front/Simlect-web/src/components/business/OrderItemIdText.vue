<template>
  <p v-if="displayId" class="order-item-id" :title="displayId">订单项 ID {{ displayId }}</p>
</template>

<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{ id?: string | number | null }>();

const displayId = computed(() => {
  if (props.id == null || props.id === '') return '';
  return String(props.id);
});
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.order-item-id {
  margin: 4px 0 0;
  font-size: 10px;
  line-height: 1.35;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: $color-text-muted;
  word-break: break-all;
}
</style>
