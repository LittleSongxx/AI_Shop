<template>
  <section class="visual-selection" aria-label="选择图片中的商品主体">
    <div v-if="card.imageAssetId && imageReadable" class="image-stage">
      <img
        :src="imageUrl"
        alt="待选择商品主体的图片"
        @error="imageReadable = false"
      />
      <button
        v-for="subject in validSubjects"
        :key="subject.subjectId"
        type="button"
        class="subject-frame"
        :class="{ selected: selectedSubjectId === subject.subjectId }"
        :style="subjectStyle(subject)"
        :disabled="disabled || expired || submittingSubjectId !== null || selectedSubjectId !== null"
        :aria-label="`选择${subject.label}`"
        @click="selectSubject(subject)"
      >
        <span>{{ subject.label }}</span>
      </button>
    </div>
    <div v-else class="subject-list">
      <button
        v-for="subject in validSubjects"
        :key="subject.subjectId"
        type="button"
        class="subject-option"
        :disabled="disabled || expired || submittingSubjectId !== null || selectedSubjectId !== null"
        @click="selectSubject(subject)"
      >
        {{ selectedSubjectId === subject.subjectId ? '已选择' : subject.label }}
      </button>
    </div>
    <p v-if="expired" class="selection-expired">图片主体选择已过期，请重新上传图片。</p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';

export interface VisualSubject {
  subjectId: string;
  label: string;
  bbox: [number, number, number, number] | number[];
}

export interface VisualSubjectSelectionCardData {
  type: 'VISUAL_SUBJECT_SELECTION';
  selectionId: string;
  imageAssetId: string;
  subjects: VisualSubject[];
  expiresAt: string;
}

const props = defineProps<{
  card: VisualSubjectSelectionCardData;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  select: [payload: {
    card: VisualSubjectSelectionCardData;
    subject: VisualSubject;
    done: (success: boolean) => void;
  }];
}>();

const imageReadable = ref(true);
const submittingSubjectId = ref<string | null>(null);
const selectedSubjectId = ref<string | null>(null);
const imageUrl = computed(() =>
  `/api/file/getAgentImage?imageAssetId=${encodeURIComponent(props.card.imageAssetId)}`
);
const expired = computed(() => {
  const value = Date.parse(props.card.expiresAt);
  return Number.isFinite(value) && value <= Date.now();
});
const validSubjects = computed(() =>
  (props.card.subjects || []).filter((subject) => {
    const [x1, y1, x2, y2] = subject?.bbox || [];
    return Boolean(
      subject?.subjectId
      && subject?.label
      && [x1, y1, x2, y2].every((value) => Number.isFinite(Number(value)))
      && Number(x1) >= 0
      && Number(y1) >= 0
      && Number(x2) <= 999
      && Number(y2) <= 999
      && Number(x2) > Number(x1)
      && Number(y2) > Number(y1)
    );
  })
);

const subjectStyle = (subject: VisualSubject) => {
  const [x1, y1, x2, y2] = subject.bbox.map((value) => Number(value));
  return {
    left: `${(x1 / 999) * 100}%`,
    top: `${(y1 / 999) * 100}%`,
    width: `${((x2 - x1) / 999) * 100}%`,
    height: `${((y2 - y1) / 999) * 100}%`
  };
};

const selectSubject = (subject: VisualSubject) => {
  if (props.disabled || expired.value || submittingSubjectId.value || selectedSubjectId.value) return;
  submittingSubjectId.value = subject.subjectId;
  emit('select', {
    card: props.card,
    subject,
    done: (success) => {
      submittingSubjectId.value = null;
      if (success) selectedSubjectId.value = subject.subjectId;
    }
  });
};
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.visual-selection {
  width: min(440px, 72vw);
  max-width: 100%;
}

.image-stage {
  position: relative;
  overflow: hidden;
  border: 1px solid $color-border;
  border-radius: $radius-xs;
  background: #f7f8fa;

  > img {
    display: block;
    width: 100%;
    max-height: 340px;
    object-fit: contain;
  }
}

.subject-frame {
  position: absolute;
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  min-width: 18px;
  min-height: 18px;
  padding: 0;
  border: 2px solid $color-primary;
  border-radius: 3px;
  background: rgba($color-primary, 0.1);
  color: #fff;
  cursor: pointer;

  span {
    max-width: 100%;
    padding: 2px 5px;
    overflow: hidden;
    border-radius: 0 0 3px 0;
    background: $color-primary;
    font-size: 11px;
    line-height: 1.25;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &:focus-visible {
    outline: 2px solid #fff;
    outline-offset: -4px;
  }

  &:hover:not(:disabled),
  &.selected {
    border-color: #e55d2a;
    background: rgba(229, 93, 42, 0.16);

    span {
      background: #e55d2a;
    }
  }

  &:disabled {
    cursor: not-allowed;
  }
}

.subject-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.subject-option {
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid $color-primary;
  border-radius: $radius-xs;
  background: #fff;
  color: $color-primary;
  cursor: pointer;
  font-size: 12px;

  &:disabled {
    cursor: not-allowed;
    opacity: 0.6;
  }
}

.selection-expired {
  margin: 8px 0 0;
  color: $color-text-muted;
  font-size: 11px;
}

@media (max-width: 520px) {
  .visual-selection {
    width: min(100%, 82vw);
  }
}
</style>
