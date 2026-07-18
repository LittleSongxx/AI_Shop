<template>
  <el-rate v-model="innerValue" allow-half :disabled="readonly" @change="$emit('update:modelValue', innerValue)" />
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';
const props = defineProps<{ modelValue: number; readonly?: boolean }>();
defineEmits<{ 'update:modelValue': [number] }>();
const innerValue = ref(props.modelValue || 0);
watch(
  () => props.modelValue,
  (v) => (innerValue.value = v)
);
</script>
