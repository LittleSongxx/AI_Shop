<template>
  <Teleport to="body">
    <Transition name="pc-agent-fade">
      <div
        v-if="pcAgentPanel.visible"
        class="pc-agent-float-root ignore"
        role="dialog"
        aria-label="智能客服"
        @click.self="pcAgentPanel.close()"
      >
        <section class="pc-agent-float-panel agent-page" @click.stop>
          <header class="pc-agent-float-head">
            <div class="head-title">
              <el-icon class="head-icon" :size="18"><ChatDotRound /></el-icon>
              <span>智能客服</span>
            </div>
            <button type="button" class="btn-close" aria-label="关闭" @click="pcAgentPanel.close()">
              <el-icon :size="18"><Close /></el-icon>
            </button>
          </header>
          <div v-if="sessionReady" class="pc-agent-float-body">
            <AgentChatList />
            <AgentSendPanel />
          </div>
          <div v-else class="pc-agent-float-loading" v-loading="true" />
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { provide, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { agentComposerEmbeddedKey } from '@/composables/agentEmbed';
import { ChatDotRound, Close } from '@element-plus/icons-vue';
import AgentChatList from '@/views/agent/AgentChatList.vue';
import AgentSendPanel from '@/views/agent/AgentSendPanel.vue';
import { useAgentSession } from '@/composables/useAgentSession';
import { usePcAgentPanelStore } from '@/stores/pcAgentPanel';

provide(agentComposerEmbeddedKey, true);

const pcAgentPanel = usePcAgentPanelStore();
const route = useRoute();
const { start, stop } = useAgentSession();
const sessionReady = ref(false);

watch(
  () => route.fullPath,
  (currentPath, previousPath) => {
    if (currentPath !== previousPath && pcAgentPanel.visible) {
      pcAgentPanel.close();
    }
  }
);

watch(
  () => pcAgentPanel.visible,
  async (open) => {
    if (open) {
      sessionReady.value = false;
      await start();
      sessionReady.value = true;
    } else {
      sessionReady.value = false;
      stop();
    }
  }
);
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.pc-agent-float-root {
  position: fixed;
  inset: 0;
  z-index: $z-index-float-agent;
  background: rgba(16, 24, 40, 0.28);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 24px;
  box-sizing: border-box;
}

.pc-agent-float-panel {
  flex: 0 0 auto;
  align-self: center;
  width: min(420px, calc(100vw - 48px));
  height: min(680px, calc(100vh - 80px));
  min-height: 520px;
  max-width: 420px;
  display: flex;
  flex-direction: column;
  background: $color-card;
  border-radius: 8px;
  box-shadow: 0 12px 40px rgba(16, 24, 40, 0.18);
  overflow: hidden;
  border: 1px solid $color-border-gray;
}

.pc-agent-float-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid $color-border-gray;
  background: linear-gradient(90deg, rgba($color-primary, 0.08), transparent);

  .head-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 15px;
    font-weight: 600;
    color: $color-text-title;
  }

  .head-icon {
    color: $color-primary;
  }

  .btn-close {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border: none;
    border-radius: $radius-sm;
    background: transparent;
    color: $color-text-muted;
    cursor: pointer;

    &:hover {
      background: $color-bg-subtle;
      color: $color-text-title;
    }
  }
}

.pc-agent-float-body {
  flex: 1 1 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;

  :deep(.chat-scroll) {
    flex: 1 1 0;
    min-height: 0;
    padding-bottom: 12px !important;
    background: #fafafa;
  }

  :deep(.agent-composer-stack) {
    position: static !important;
    left: auto !important;
    right: auto !important;
    bottom: auto !important;
    z-index: 1 !important;
    flex-shrink: 0;
    width: 100% !important;
    max-width: 100% !important;
    margin: 0 !important;
    padding: 0 !important;
    pointer-events: auto !important;
    gap: 0 !important;
  }

  :deep(.consult-product-float) {
    margin: 0;
    border-radius: 0;
    border-bottom: 1px solid $color-border-light;
    box-shadow: none;
  }

  :deep(.agent-composer-dock) {
    border-top: 1px solid $color-border-gray;
    box-shadow: none;
  }

  :deep(.quick-tips) {
    padding: 8px 10px;
  }

  :deep(.chat-input-bar) {
    padding: 8px 10px;
  }

  :deep(.agent-products) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    max-height: 220px;
  }

  :deep(.product-link) {
    padding: 6px;
  }

  :deep(.product-link .product-image) {
    width: 52px !important;
    height: 52px !important;
    margin: 0 auto;
  }

  :deep(.consult-product-card .cover-wrap) {
    flex: 0 0 48px;
    width: 48px;
    height: 48px;
  }
}

.pc-agent-float-loading {
  flex: 1;
  min-height: 200px;
}

.pc-agent-fade-enter-active,
.pc-agent-fade-leave-active {
  transition: opacity 0.2s ease;

  .pc-agent-float-panel {
    transition: transform 0.22s ease, opacity 0.2s ease;
  }
}

.pc-agent-fade-enter-from,
.pc-agent-fade-leave-to {
  opacity: 0;

  .pc-agent-float-panel {
    transform: scale(0.96);
    opacity: 0;
  }
}
</style>
