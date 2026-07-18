<template>
  <div
    v-if="html"
    ref="rootRef"
    class="markdown-content"
    :class="{ 'is-image-center': centerImages }"
    v-html="html"
    @click="onContentClick"
  />
  <p v-else class="markdown-empty">{{ emptyText }}</p>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import MarkdownIt from 'markdown-it';
import { openImagePreview } from '@/composables/imagePreview';
import { renderAgentMessageHtml } from '@/utils/agentMessageRender';
import { normalizeProductDesc } from '@/utils/productDesc';

const props = withDefaults(
  defineProps<{
    content?: string | null;
    emptyText?: string;

    agentRich?: boolean;

    centerImages?: boolean;
  }>(),
  { emptyText: '暂无详情', agentRich: false, centerImages: false }
);

const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true
});

const html = computed(() => {
  const normalized = normalizeProductDesc(props.content);
  if (!normalized) return '';
  try {
    if (props.agentRich) return renderAgentMessageHtml(normalized);
    return md.render(normalized);
  } catch (err) {
    console.warn('Markdown 渲染失败，已降级为纯文本', err);
    return normalized
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\n/g, '<br>');
  }
});

const rootRef = ref<HTMLElement>();

const onContentClick = (event: MouseEvent) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const img = target.closest('img');
  if (!(img instanceof HTMLImageElement) || !img.src) return;
  event.preventDefault();
  const images = rootRef.value?.querySelectorAll('img');
  const urls = images ? Array.from(images).map((node) => node.src).filter(Boolean) : [img.src];
  const index = urls.indexOf(img.src);
  openImagePreview(urls, index >= 0 ? index : 0);
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.markdown-content {
  font-size: 14px;
  line-height: 1.6;
  color: $color-text-body;
  word-break: break-word;

  :deep(img) {
    display: block;
    max-width: 100%;
    height: auto;
    margin: 8px 0;
    border-radius: $radius-xs;
    cursor: zoom-in;
  }

  :deep(p) {
    margin: 8px 0;
  }

  :deep(a) {
    color: $color-primary;
    text-decoration: none;
  }

  :deep(table) {
    width: 100%;
    margin: 10px 0;
    border-collapse: collapse;
    font-size: 13px;
    line-height: 1.45;
  }

  :deep(th),
  :deep(td) {
    border: 1px solid $color-border;
    padding: 8px 10px;
    text-align: left;
    vertical-align: top;
  }

  :deep(th) {
    background: $color-bg-subtle;
    color: $color-text-title;
    font-weight: 600;
  }

  :deep(td) {
    color: $color-text-body;
  }

  &.is-image-center {
    :deep(img) {
      margin-left: auto;
      margin-right: auto;
    }

    :deep(p:has(> img:only-child)),
    :deep(figure) {
      text-align: center;
    }
  }
}

.markdown-empty {
  margin: 0;
  font-size: 13px;
  color: $color-text-muted;
}
</style>
