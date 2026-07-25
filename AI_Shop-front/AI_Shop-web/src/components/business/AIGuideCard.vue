<template>
  <div class="ai-guide-card" @click="goAgent">
    <div class="ai-guide-left">
      <div class="ai-avatar-pulse">
        <el-icon :size="18"><ChatDotRound /></el-icon>
      </div>
      <div class="ai-text">
        <p class="ai-title">AI 帮你选</p>
        <p class="ai-sub">不知道买什么？告诉我你的需求</p>
      </div>
    </div>
    <div class="ai-prompts">
      <button
        v-for="(prompt, i) in samplePrompts"
        :key="i"
        type="button"
        class="ai-prompt-chip"
        @click.stop="quickAsk(prompt)"
      >
        {{ prompt }}
      </button>
    </div>
    <div class="ai-arrow">
      <el-icon :size="14"><ArrowRight /></el-icon>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { ArrowRight, ChatDotRound } from '@element-plus/icons-vue';
import { useOpenAgent } from '@/composables/useOpenAgent';

const PROMPT_POOL = [
  '通勤路上适合用什么',
  '厨房好物推荐',
  '宿舍生活必备品',
  '运动健身装备推荐',
  '百元以内的实用好物',
  '适合送给妈妈的礼物',
  '居家收纳好物',
  '学生党平价好物',
  '数码周边推荐',
  '秋冬保暖好物',
  '宠物用品推荐',
  '旅行出行必备',
  '适合送给男朋友的礼物',
  '办公桌改造好物',
  '卧室好物推荐',
  '夏天清凉好物',
  '100元内的创意礼物',
  '健康养生好物',
  '宝宝用品推荐',
  '车载好物推荐'
];

function pickRandom<T>(arr: T[], count: number): T[] {
  const shuffled = [...arr];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled.slice(0, count);
}

const samplePrompts = ref<string[]>([]);

onMounted(() => {
  samplePrompts.value = pickRandom(PROMPT_POOL, 3);
});

const { openAgent } = useOpenAgent();

const goAgent = () => {
  openAgent();
};

const quickAsk = (prompt: string) => {
  openAgent({ presetMessage: prompt });
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.ai-guide-card {
  margin: 12px $app-page-gutter 0;
  padding: 12px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  background: linear-gradient(135deg, #ffffff 0%, #fefaf3 100%);
  border: 1px solid rgba(212, 162, 78, 0.18);
  border-radius: $radius-card;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;

  &:hover {
    border-color: rgba(212, 162, 78, 0.35);
    box-shadow: $shadow-card;
  }

  &:active {
    transform: scale(0.985);
  }
}

.ai-guide-left {
  display: flex;
  align-items: center;
  gap: 10px;
  z-index: 1;
  flex-shrink: 0;
  flex: 1;
  min-width: 0;
}

.ai-avatar-pulse {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: $color-gold-soft;
  color: $color-gold;
  display: grid;
  place-items: center;
  box-shadow: 0 0 0 3px rgba(212, 162, 78, 0.1);
  animation: aiPulse 3s ease-in-out infinite;
}

@keyframes aiPulse {
  0%, 100% { box-shadow: 0 0 0 3px rgba(212, 162, 78, 0.1); }
  50% { box-shadow: 0 0 0 6px rgba(212, 162, 78, 0.06); }
}

.ai-text {
  .ai-title {
    font-size: 15px;
    font-weight: 600;
    color: $color-text-title;
    margin: 0;
    line-height: 1.3;
  }
  .ai-sub {
    font-size: 12px;
    color: $color-text-muted;
    margin: 2px 0 0;
    line-height: 1.2;
  }
}

.ai-prompts {
  display: flex;
  gap: 6px;
  z-index: 1;
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}

.ai-prompt-chip {
  padding: 4px 10px;
  border: 1px solid rgba(212, 162, 78, 0.25);
  border-radius: $radius-pill;
  background: rgba(212, 162, 78, 0.06);
  color: #8b7355;
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.2s, border-color 0.2s, color 0.2s;

  &:hover {
    background: rgba(212, 162, 78, 0.14);
    border-color: rgba(212, 162, 78, 0.4);
    color: $color-gold;
  }

  &:active {
    background: $color-gold-soft;
    border-color: $color-gold;
    color: $color-gold;
  }
}

.ai-arrow {
  z-index: 1;
  color: $color-gold;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  opacity: 0.5;
  transition: opacity 0.2s;

  .ai-guide-card:hover & {
    opacity: 1;
  }
}

@media (max-width: 400px) {
  .ai-guide-card {
    flex-direction: column;
    align-items: stretch;
  }

  .ai-guide-left {
    flex: none;
  }

  .ai-prompts {
    width: 100%;
    justify-content: center;
  }

  .ai-arrow {
    display: none;
  }
}
</style>