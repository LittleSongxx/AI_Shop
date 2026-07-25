<template>
  <el-avatar :size="size" :src="avatarSrc" class="user-avatar">
    <el-icon :size="iconSize"><UserFilled /></el-icon>
  </el-avatar>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { UserFilled } from '@element-plus/icons-vue';
import { resolveAvatarUrl } from '@/utils/image';

const props = withDefaults(
  defineProps<{
    avatar?: string | null;
    size?: number;
  }>(),
  { size: 64 }
);

const avatarSrc = computed(() => resolveAvatarUrl(props.avatar));
const iconSize = computed(() => Math.floor(props.size * 0.45));
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.user-avatar {
  background: linear-gradient(135deg, $color-primary-soft, $color-bg-subtle);
  color: $color-primary;
  flex-shrink: 0;
  box-shadow: 0 4px 12px rgba($color-primary, 0.15);
}
</style>
