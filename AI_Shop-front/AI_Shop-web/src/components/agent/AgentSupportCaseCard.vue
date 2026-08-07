<template>
  <section class="support-card" :aria-label="isDetail ? '工单详情' : '工单列表'">
    <header class="support-head">
      <div>
        <p class="support-kicker">售后服务</p>
        <h3>{{ isDetail ? '工单详情' : '我的工单' }}</h3>
      </div>
      <button v-if="!isDetail" type="button" class="link-button" @click="openCases">全部工单</button>
    </header>

    <div v-if="isDetail && detailCase" class="case-detail">
      <CaseSummary :item="detailCase" />
      <Evidence :item="detailCase" />
      <button type="button" class="case-link" @click="openCase(detailCase)">查看工单页面</button>
    </div>
    <div v-else class="case-list">
      <button
        v-for="item in cases"
        :key="String(item.caseId || item.caseNo)"
        type="button"
        class="case-row"
        @click="openCase(item)"
      >
        <span class="case-row-main">
          <strong>{{ item.categoryLabel || item.category || '售后工单' }}</strong>
          <span>{{ item.description || '暂无描述' }}</span>
        </span>
        <span class="case-row-side">
          <span class="status" :class="statusClass(item.status)">{{ statusText(item.status) }}</span>
          <small>{{ item.caseNo || item.caseId }}</small>
        </span>
      </button>
      <p v-if="!cases.length" class="empty">暂无工单记录</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';

const props = defineProps<{ card: Record<string, any> }>();
const router = useRouter();
const isDetail = computed(() => props.card?.type === 'SUPPORT_CASE_DETAIL');
const detailCase = computed(() => (props.card?.case && typeof props.card.case === 'object' ? props.card.case : null));
const cases = computed(() => (Array.isArray(props.card?.cases) ? props.card.cases : []) as Record<string, any>[]);

const statusText = (status: unknown) => ({
  OPEN: '待处理',
  IN_PROGRESS: '处理中',
  RESOLVED: '已解决',
  CANCELLED: '已取消'
}[String(status || '').toUpperCase()] || '待处理');
const statusClass = (status: unknown) => `status-${String(status || 'OPEN').toLowerCase()}`;
const openCases = () => void router.push('/support-cases');
const openCase = (item: Record<string, any>) => {
  const id = item?.caseNo || item?.caseId;
  if (id != null && id !== '') void router.push({ path: '/support-cases', query: { caseId: String(id) } });
};
</script>

<script lang="ts">
import { defineComponent, h } from 'vue';
import { resolveImageUrl } from '@/utils/image';

const CaseSummary = defineComponent({
  props: { item: { type: Object, required: true } },
  setup(props) {
    return () => h('div', { class: 'case-summary' }, [
      h('div', { class: 'summary-line' }, [
        h('span', { class: ['status', `status-${String((props.item as any).status || 'OPEN').toLowerCase()}`] },
          ({ OPEN: '待处理', IN_PROGRESS: '处理中', RESOLVED: '已解决', CANCELLED: '已取消' } as any)[String((props.item as any).status || '').toUpperCase()] || '待处理'),
        h('small', {}, (props.item as any).caseNo || (props.item as any).caseId || '')
      ]),
      h('p', { class: 'case-description' }, (props.item as any).description || '暂无描述'),
      h('p', { class: 'case-meta' }, [
        (props.item as any).categoryLabel || (props.item as any).category || '其他',
        (props.item as any).orderId ? ` · 订单 ${(props.item as any).orderId}` : ''
      ])
    ]);
  }
});

const Evidence = defineComponent({
  props: { item: { type: Object, required: true } },
  setup(props) {
    return () => {
      const evidence = ((props.item as any).evidence || {}) as Record<string, any>;
      const path = evidence.path ? resolveImageUrl(evidence.path, { useThumbnail: true }) : '';
      if (!path && !evidence.vlmDescription && !evidence.moderationStatus) return null;
      return h('div', { class: 'evidence' }, [
        path ? h('img', { src: path, alt: '售后图片证据' }) : null,
        h('span', {}, evidence.vlmDescription || `图片审核：${evidence.moderationStatus || '已记录'}`)
      ]);
    };
  }
});

export default { components: { CaseSummary, Evidence } };
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.support-card {
  width: 100%;
  min-width: 0;
  overflow: hidden;
  border: 1px solid $color-border;
  border-radius: $radius-sm;
  background: #fff;
}
.support-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid $color-border-gray;
  h3 { margin: 2px 0 0; font-size: 14px; color: $color-text-title; }
}
.support-kicker { margin: 0; color: $color-primary; font-size: 11px; }
.link-button,
.case-link { border: 0; background: transparent; color: $color-primary; font-size: 12px; cursor: pointer; }
.case-list { padding: 4px 12px; }
.case-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 10px 0;
  border: 0;
  border-bottom: 1px solid $color-border-light;
  background: transparent;
  color: $color-text-body;
  text-align: left;
  cursor: pointer;
  &:last-child { border-bottom: 0; }
}
.case-row-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.case-row-main strong { color: $color-text-title; font-size: 12px; }
.case-row-main span { overflow: hidden; font-size: 12px; line-height: 1.4; text-overflow: ellipsis; white-space: nowrap; }
.case-row-side { display: flex; flex: 0 0 auto; flex-direction: column; align-items: flex-end; gap: 4px; }
.case-row-side small, .summary-line small { color: $color-text-muted; font-size: 10px; }
.status { display: inline-flex; align-items: center; min-height: 20px; padding: 0 6px; border-radius: $radius-pill; font-size: 10px; white-space: nowrap; }
.status-open { color: $color-warning; background: $color-warning-soft; }
.status-in_progress { color: $color-info; background: $color-info-soft; }
.status-resolved { color: $color-success; background: $color-success-soft; }
.status-cancelled { color: $color-text-muted; background: $color-surface-inset; }
.empty { margin: 12px 0; color: $color-text-muted; text-align: center; font-size: 12px; }
.case-detail { padding: 12px; }
.case-summary { display: flex; flex-direction: column; gap: 7px; }
.summary-line { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.case-description { margin: 0; color: $color-text-body; font-size: 13px; line-height: 1.55; white-space: pre-wrap; }
.case-meta { margin: 0; color: $color-text-muted; font-size: 11px; }
.evidence { display: flex; align-items: flex-start; gap: 8px; margin-top: 10px; color: $color-text-muted; font-size: 11px; line-height: 1.4; }
.evidence img { width: 64px; height: 64px; flex: 0 0 auto; border-radius: $radius-sm; object-fit: cover; }
.case-link { margin-top: 12px; padding: 0; }
</style>
