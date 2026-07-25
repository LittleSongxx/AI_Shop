<template>
  <el-dialog
    v-model="visible"
    title="举报评论"
    width="92%"
    :style="{ maxWidth: '440px' }"
    destroy-on-close
    @closed="reset"
  >
    <p v-if="snapshot" class="report-target">
      <span class="target-label">举报内容</span>
      <span class="target-text">{{ snapshot }}</span>
    </p>

    <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
      <el-form-item label="举报理由" prop="reason">
        <div class="reason-grid">
          <button
            v-for="r in reasons"
            :key="r"
            type="button"
            class="reason-chip"
            :class="{ active: form.reason === r }"
            @click="form.reason = r"
          >
            {{ r }}
          </button>
        </div>
      </el-form-item>
      <el-form-item label="补充说明（选填）">
        <el-input
          v-model="form.detail"
          type="textarea"
          :rows="3"
          maxlength="500"
          show-word-limit
          placeholder="可补充具体问题，便于平台核实"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <div class="footer-actions">
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">提交举报</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue';
import type { FormInstance, FormRules } from 'element-plus';
import { commentReportApi } from '@/api/modules';
import { toast } from '@/utils/toast';

const reasons = ['广告/垃圾信息', '辱骂攻击', '违法违规', '不实信息', '泄露隐私', '其他'];

const visible = ref(false);
const submitting = ref(false);
const formRef = ref<FormInstance>();
const snapshot = ref('');

const form = reactive({
  orderId: '',
  productId: '',
  reason: '',
  detail: ''
});

const rules: FormRules = {
  reason: [{ required: true, message: '请选择举报理由', trigger: 'change' }]
};

const reset = () => {
  form.orderId = '';
  form.productId = '';
  form.reason = '';
  form.detail = '';
  snapshot.value = '';
  formRef.value?.clearValidate();
};

const show = (payload: { orderId: string; productId?: string; commentContent?: string }) => {
  reset();
  form.orderId = String(payload.orderId || '');
  form.productId = String(payload.productId || '');
  snapshot.value = String(payload.commentContent || '').slice(0, 1000);
  visible.value = true;
};

const submit = async () => {
  const valid = await formRef.value?.validate().catch(() => false);
  if (!valid) return;
  submitting.value = true;
  try {
    await commentReportApi.submitReport({
      orderId: form.orderId,
      productId: form.productId || undefined,
      reason: form.reason,
      detail: form.detail.trim() || undefined,
      commentSnapshot: snapshot.value || undefined
    });
    toast.success('举报已提交，平台会尽快核实');
    visible.value = false;
  } finally {
    submitting.value = false;
  }
};

defineExpose({ show });
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.report-target {
  margin: 0 0 14px;
  padding: 10px 12px;
  border-radius: $radius-btn;
  background: $color-bg-subtle;
  border: 1px solid $color-border-light;
  font-size: 13px;
  line-height: 1.5;

  .target-label {
    display: block;
    margin-bottom: 4px;
    font-size: 12px;
    font-weight: 600;
    color: $color-text-muted;
  }

  .target-text {
    color: $color-text-body;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
}

.reason-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.reason-chip {
  padding: 7px 14px;
  border: 1px solid $color-border-gray;
  border-radius: $radius-pill;
  background: $color-card;
  color: $color-text-body;
  font-size: 13px;
  cursor: pointer;
  transition: border-color $transition-fast, color $transition-fast, background $transition-fast;

  &:hover {
    border-color: rgba($color-primary, 0.45);
  }

  &.active {
    border-color: $color-primary;
    color: $color-primary;
    background: $color-primary-soft;
    font-weight: 600;
  }
}

.footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  width: 100%;
}
</style>
