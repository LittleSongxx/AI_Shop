<template>
  <div class="bubble-row user" :class="{ 'is-product-card': parsed.card }">
    <AgentConsultProductCard
      v-if="parsed.card"
      :product="parsed.card"
      variant="bubble"
    />
    <div v-else class="bubble user-bubble">
      <p v-if="parsed.text" class="text">{{ parsed.text }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import AgentConsultProductCard from '@/components/agent/AgentConsultProductCard.vue';
import { parseProductConsultMessage } from '@/utils/agentProductConsult';

const props = defineProps<{
  userMessage?: string | null;
}>();

const parsed = computed(() => parseProductConsultMessage(props.userMessage));
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
}
</style>
