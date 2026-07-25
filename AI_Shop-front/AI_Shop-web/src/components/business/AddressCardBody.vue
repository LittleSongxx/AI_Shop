<template>
  <div class="card-main">
    <div class="card-head">
      <span class="name">{{ item.addressee }}</span>
      <span class="phone">{{ item.phone }}</span>
      <span v-if="item.defaultType === 1" class="default-tag">默认</span>
    </div>
    <p class="detail">{{ item.address }}</p>
  </div>
</template>

<script setup lang="ts">
import type { AddressFormItem } from '@/components/business/AddressFormPanel.vue';

defineProps<{ item: AddressFormItem }>();
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.card-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.name {
  font-size: 16px;
  font-weight: 600;
  color: $color-text-title;
}

.phone {
  font-size: 14px;
  color: $color-text-body;
}

.default-tag {
  font-size: 11px;
  line-height: 1;
  padding: 3px 6px;
  border-radius: $radius-xs;
  color: $color-primary;
  background: rgba($color-primary, 0.12);
  font-weight: 600;
}

.detail {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
  color: $color-text-body;
  word-break: break-all;
}
</style>
