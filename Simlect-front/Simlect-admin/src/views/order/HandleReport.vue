<template>
  <el-dialog v-model="visible" title="处理举报" width="500px" destroy-on-close @closed="reset">
    <div class="report-detail">
      <p class="detail-row"><span class="label">订单号：</span>{{ form.orderId }}</p>
      <p class="detail-row"><span class="label">举报人：</span>{{ form.reporterUserId }}</p>
      <p class="detail-row"><span class="label">举报理由：</span>{{ form.reason }}</p>
      <p class="detail-row"><span class="label">补充说明：</span>{{ form.detail || '无' }}</p>
      <p class="detail-row"><span class="label">评论内容：</span>{{ form.commentSnapshot || '无' }}</p>
    </div>

    <el-form :model="form" label-position="top" style="margin-top: 16px;">
      <el-form-item label="处理备注">
        <el-input
          v-model="form.handleRemark"
          type="textarea"
          :rows="3"
          maxlength="500"
          show-word-limit
          placeholder="请输入处理说明"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <div class="footer-actions">
        <el-button @click="visible = false">取消</el-button>
        <el-button type="danger" :loading="submitting" @click="submit(2)">驳回举报</el-button>
        <el-button type="primary" :loading="submitting" @click="submit(1)">确认处理</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref, getCurrentInstance } from 'vue'

const { proxy } = getCurrentInstance()
const emit = defineEmits(['reload'])

const visible = ref(false)
const submitting = ref(false)

const form = reactive({
  reportId: '',
  orderId: '',
  reporterUserId: '',
  reason: '',
  detail: '',
  commentSnapshot: '',
  handleRemark: ''
})

const reset = () => {
  Object.assign(form, {
    reportId: '',
    orderId: '',
    reporterUserId: '',
    reason: '',
    detail: '',
    commentSnapshot: '',
    handleRemark: ''
  })
}

const show = (row) => {
  reset()
  form.reportId = row.reportId
  form.orderId = row.orderId || ''
  form.reporterUserId = row.reporterUserId || ''
  form.reason = row.reason || ''
  form.detail = row.detail || ''
  form.commentSnapshot = row.commentSnapshot || ''
  visible.value = true
}

const submit = async (status) => {
  submitting.value = true
  try {
    const result = await proxy.Request({
      url: proxy.Api.handleCommentReport,
      params: {
        reportId: form.reportId,
        status: status,
        handleRemark: form.handleRemark || undefined
      }
    })
    if (!result) return
    proxy.Message.success(status === 1 ? '已确认处理' : '已驳回举报')
    visible.value = false
    emit('reload')
  } finally {
    submitting.value = false
  }
}

defineExpose({ show })
</script>

<style lang="scss" scoped>
.report-detail {
  padding: 12px 14px;
  border-radius: 8px;
  background: #f5f5f7;
  border: 1px solid #e8e8ed;

  .detail-row {
    margin: 0 0 8px;
    font-size: 13px;
    line-height: 1.5;
    color: var(--text);
    word-break: break-all;

    &:last-child {
      margin-bottom: 0;
    }

    .label {
      font-weight: 600;
      color: var(--text2);
    }
  }
}

.footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
