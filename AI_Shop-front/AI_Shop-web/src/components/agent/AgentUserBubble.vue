<template>
  <div class="bubble-row user" :class="{ 'is-product-card': parsed.card }">
    <AgentConsultProductCard
      v-if="parsed.card"
      :product="parsed.card"
      variant="bubble"
    />
    <div v-else class="bubble user-bubble">
      <figure v-if="imageAssetId" class="agent-image">
        <img
          v-if="imageReadable"
          :src="agentImageUrl"
          alt="用户上传的商品图片"
          @error="imageReadable = false"
        />
        <figcaption v-else>图片已按保留策略清理</figcaption>
      </figure>
      <p v-if="parsed.text" class="text">{{ parsed.text }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import AgentConsultProductCard from '@/components/agent/AgentConsultProductCard.vue';
import { parseProductConsultMessage } from '@/utils/agentProductConsult';

const props = defineProps<{
  userMessage?: string | null;
  imageAssetId?: string | null;
}>();

const parsed = computed(() => parseProductConsultMessage(props.userMessage));
const imageReadable = ref(true);
const agentImageUrl = computed(() =>
  props.imageAssetId
    ? `/api/file/getAgentImage?imageAssetId=${encodeURIComponent(props.imageAssetId)}`
    : ''
);

watch(
  () => props.imageAssetId,
  () => {
    imageReadable.value = true;
  },
  { immediate: true }
);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.bubble-row.user {
  display: flex;
  justify-content: flex-end;
  width: 100%;
  box-sizing: border-box;
  margin-bottom: 8px;
}

.bubble {
  width: fit-content;
  max-width: min(75%, 520px);
  padding: 9px 12px;
  font-size: 13px;
  line-height: 1.45;
  border-radius: 8px;
  word-break: break-word;
  flex: 0 1 auto;
}

.bubble-row.user.is-product-card {
  margin-bottom: 8px;
}

.user-bubble {
  background: $color-primary;
  color: #fff;
  border-bottom-right-radius: 4px;

  .text {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .agent-image {
    max-width: min(280px, 100%);
    margin: 0 0 8px;

    img {
      display: block;
      width: 100%;
      max-height: 300px;
      border-radius: 5px;
      object-fit: contain;
      background: rgba(255, 255, 255, 0.16);
    }

    figcaption {
      padding: 8px 10px;
      border: 1px solid rgba(255, 255, 255, 0.35);
      border-radius: 5px;
      color: rgba(255, 255, 255, 0.9);
      font-size: 12px;
    }
  }
}
</style>
