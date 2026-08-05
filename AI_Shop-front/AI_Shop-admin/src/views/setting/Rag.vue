<template>
  <div class="knowledge-page">
    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <el-tab-pane label="FAQ 知识" name="faq">
        <div class="tab-body">
          <div class="toolbar">
            <el-form :model="faqSearch" inline @submit.prevent>
              <el-form-item label="问题">
                <el-input
                  v-model="faqSearch.questionFuzzy"
                  clearable
                  placeholder="输入问题"
                  @keyup.enter="loadFaqList"
                />
              </el-form-item>
              <el-form-item label="分类">
                <el-input
                  v-model="faqSearch.category"
                  clearable
                  placeholder="如 logistics"
                  @keyup.enter="loadFaqList"
                />
              </el-form-item>
              <el-form-item label="状态">
                <el-select v-model="faqSearch.publishStatus" clearable>
                  <el-option label="已发布" value="PUBLISHED" />
                  <el-option label="草稿" value="DRAFT" />
                  <el-option label="已归档" value="ARCHIVED" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button :icon="Search" type="primary" @click="loadFaqList">搜索</el-button>
                <el-button :icon="Plus" type="success" @click="showEdit()">新增</el-button>
              </el-form-item>
            </el-form>
          </div>
          <div class="table-panel">
            <Table :columns="faqColumns" :fetch="loadFaqList" :dataSource="faqData">
              <template #slotSimilarQuestion="{ row }">
                <div v-for="(item, index) in row.similarQuestion" :key="`${row.questionId}-${index}`">
                  {{ index + 1 }}、{{ item }}
                </div>
                <span v-if="!row.similarQuestion?.length">—</span>
              </template>
              <template #slotFaqStatus="{ row }">
                <el-tag :type="knowledgeStatusType(row.publishStatus)">
                  {{ knowledgeStatusText(row.publishStatus) }}
                </el-tag>
              </template>
              <template #slotFaqOperation="{ row }">
                <div class="row-actions">
                  <el-button link type="primary" @click="showEdit(row)">编辑</el-button>
                  <el-button link type="danger" @click="delRag(row)">删除</el-button>
                </div>
              </template>
            </Table>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="知识文档" name="documents">
        <div class="tab-body">
          <div class="toolbar">
            <el-form :model="documentSearch" inline @submit.prevent>
              <el-form-item label="状态">
                <el-select v-model="documentSearch.status" clearable>
                  <el-option label="待发布" value="READY" />
                  <el-option label="已发布" value="PUBLISHED" />
                  <el-option label="解析失败" value="ERROR" />
                  <el-option label="已归档" value="ARCHIVED" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button :icon="Refresh" type="primary" @click="loadDocuments">刷新</el-button>
                <el-button :icon="Upload" type="success" @click="openUpload">上传文档</el-button>
              </el-form-item>
            </el-form>
          </div>
          <div class="table-panel">
            <Table :columns="documentColumns" :fetch="loadDocuments" :dataSource="documentData">
              <template #slotDocumentStatus="{ row }">
                <el-tag :type="knowledgeStatusType(row.status)">
                  {{ knowledgeStatusText(row.status) }}
                </el-tag>
              </template>
              <template #slotDocumentError="{ row }">
                <span :class="{ 'error-text': row.errorMessage }">
                  {{ clipText(row.errorMessage, 120) }}
                </span>
              </template>
              <template #slotDocumentOperation="{ row }">
                <div class="row-actions">
                  <el-button
                    v-if="row.status === 'READY'"
                    link
                    type="primary"
                    @click="publishDocument(row)"
                  >
                    发布
                  </el-button>
                  <el-button
                    v-if="row.status !== 'ARCHIVED'"
                    link
                    type="danger"
                    @click="archiveDocument(row)"
                  >
                    归档
                  </el-button>
                </div>
              </template>
            </Table>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="FAQ 候选" name="candidates">
        <div class="tab-body">
          <div class="toolbar">
            <el-form :model="candidateSearch" inline @submit.prevent>
              <el-form-item label="状态">
                <el-select v-model="candidateSearch.status" clearable>
                  <el-option label="待审核" value="PENDING" />
                  <el-option label="已通过" value="APPROVED" />
                  <el-option label="已拒绝" value="REJECTED" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button :icon="Refresh" type="primary" @click="loadCandidates">刷新</el-button>
              </el-form-item>
            </el-form>
          </div>
          <div class="table-panel">
            <Table :columns="candidateColumns" :fetch="loadCandidates" :dataSource="candidateData">
              <template #slotCandidateQuestion="{ row }">
                <span>{{ clipText(row.question, 140) }}</span>
              </template>
              <template #slotCandidateAnswer="{ row }">
                <span>{{ clipText(row.answer, 180) }}</span>
              </template>
              <template #slotCandidateStatus="{ row }">
                <el-tag :type="candidateStatusType(row.status)">
                  {{ candidateStatusText(row.status) }}
                </el-tag>
              </template>
              <template #slotCandidateOperation="{ row }">
                <div v-if="row.status === 'PENDING'" class="row-actions">
                  <el-button link type="primary" @click="openCandidateReview(row)">审核</el-button>
                  <el-button link type="danger" @click="rejectCandidate(row)">拒绝</el-button>
                </div>
              </template>
            </Table>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="入库任务" name="jobs">
        <div class="tab-body">
          <div class="toolbar">
            <el-form :model="jobSearch" inline @submit.prevent>
              <el-form-item label="状态">
                <el-select v-model="jobSearch.status" clearable>
                  <el-option label="执行中" value="RUNNING" />
                  <el-option label="待发布" value="READY" />
                  <el-option label="成功" value="SUCCESS" />
                  <el-option label="失败" value="FAILED" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button :icon="Refresh" type="primary" @click="loadJobs">刷新</el-button>
              </el-form-item>
            </el-form>
          </div>
          <div class="table-panel">
            <Table :columns="jobColumns" :fetch="loadJobs" :dataSource="jobData">
              <template #slotJobStatus="{ row }">
                <el-tag :type="jobStatusType(row.status)">{{ jobStatusText(row.status) }}</el-tag>
              </template>
              <template #slotJobProgress="{ row }">
                <el-progress :percentage="Number(row.progress || 0)" :stroke-width="8" />
              </template>
              <template #slotJobError="{ row }">
                <span :class="{ 'error-text': row.errorMessage }">
                  {{ clipText(row.errorMessage, 150) }}
                </span>
              </template>
            </Table>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>

  <RagEdit ref="ragEditRef" @reload="loadFaqList" />

  <el-dialog v-model="uploadDialog.show" title="上传知识文档" width="520px">
    <el-form label-width="80px" @submit.prevent>
      <el-form-item label="标题">
        <el-input v-model.trim="uploadDialog.title" maxlength="200" placeholder="默认使用文件名" />
      </el-form-item>
      <el-form-item label="文件" required>
        <el-upload
          drag
          action="#"
          accept=".txt,.md,.pdf,.docx"
          :auto-upload="false"
          :limit="1"
          :file-list="uploadDialog.files"
          :on-change="onKnowledgeFileChange"
          :on-remove="onKnowledgeFileRemove"
        >
          <el-icon class="upload-icon"><UploadFilled /></el-icon>
          <div>拖放文件到此处，或点击选择</div>
          <template #tip>
            <div class="upload-tip">支持 TXT、Markdown、PDF、DOCX，单文件不超过 10MB</div>
          </template>
        </el-upload>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="uploadDialog.show = false">取消</el-button>
      <el-button type="primary" :loading="uploadDialog.submitting" @click="submitDocument">
        上传并解析
      </el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="candidateDialog.show" title="审核 FAQ 候选" width="680px">
    <el-form label-width="80px" @submit.prevent>
      <el-form-item label="问题">
        <el-input :model-value="candidateDialog.question" type="textarea" :rows="2" disabled />
      </el-form-item>
      <el-form-item label="标准答案" required>
        <el-input
          v-model.trim="candidateDialog.answer"
          type="textarea"
          :rows="7"
          maxlength="1200"
          show-word-limit
        />
      </el-form-item>
      <el-form-item label="分类">
        <el-input v-model.trim="candidateDialog.category" maxlength="64" />
      </el-form-item>
      <el-form-item label="审核备注">
        <el-input
          v-model.trim="candidateDialog.remark"
          type="textarea"
          :rows="2"
          maxlength="500"
          show-word-limit
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="candidateDialog.show = false">取消</el-button>
      <el-button
        type="primary"
        :loading="candidateDialog.submitting"
        @click="approveCandidate"
      >
        通过并发布
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import {
  Plus,
  Refresh,
  Search,
  Upload,
  UploadFilled,
} from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { getCurrentInstance, ref } from 'vue'

import { validateKnowledgeFile } from '@/utils/knowledgeUpload.js'
import RagEdit from './RagEdit.vue'

const { proxy } = getCurrentInstance()
const activeTab = ref('faq')

const faqSearch = ref({ questionFuzzy: '', category: '', publishStatus: '' })
const documentSearch = ref({ status: '' })
const candidateSearch = ref({ status: 'PENDING' })
const jobSearch = ref({ status: '' })

const faqData = ref({ pageNo: 1, pageSize: 15 })
const documentData = ref({ pageNo: 1, pageSize: 15 })
const candidateData = ref({ pageNo: 1, pageSize: 15 })
const jobData = ref({ pageNo: 1, pageSize: 15 })

const faqColumns = [
  { label: '问题', prop: 'question', width: 280 },
  { label: '相似问题', prop: 'similarQuestion', scopedSlots: 'slotSimilarQuestion' },
  { label: '分类', prop: 'category', width: 120 },
  { label: '状态', prop: 'publishStatus', width: 100, scopedSlots: 'slotFaqStatus' },
  { label: '版本', prop: 'version', width: 80 },
  { label: '命中', prop: 'hitCount', width: 80 },
  { label: '更新时间', prop: 'updateTime', width: 170 },
  { label: '操作', prop: 'op', width: 110, scopedSlots: 'slotFaqOperation' },
]

const documentColumns = [
  { label: 'ID', prop: 'documentId', width: 80 },
  { label: '标题', prop: 'title', width: 220 },
  { label: '源文件', prop: 'sourceName', width: 200 },
  { label: '类型', prop: 'fileType', width: 80 },
  { label: '状态', prop: 'status', width: 100, scopedSlots: 'slotDocumentStatus' },
  { label: '版本', prop: 'version', width: 80 },
  { label: '负责人', prop: 'owner', width: 120 },
  { label: '错误', prop: 'errorMessage', scopedSlots: 'slotDocumentError' },
  { label: '更新时间', prop: 'updatedAt', width: 170 },
  { label: '操作', prop: 'op', width: 110, scopedSlots: 'slotDocumentOperation' },
]

const candidateColumns = [
  { label: 'ID', prop: 'candidateId', width: 80 },
  { label: '问题', prop: 'question', scopedSlots: 'slotCandidateQuestion' },
  { label: '答案', prop: 'answer', scopedSlots: 'slotCandidateAnswer' },
  { label: '分类', prop: 'category', width: 130 },
  { label: '频次', prop: 'frequency', width: 80 },
  { label: '状态', prop: 'status', width: 100, scopedSlots: 'slotCandidateStatus' },
  { label: '创建时间', prop: 'createdAt', width: 170 },
  { label: '操作', prop: 'op', width: 110, scopedSlots: 'slotCandidateOperation' },
]

const jobColumns = [
  { label: '任务ID', prop: 'jobId', width: 90 },
  { label: '文档ID', prop: 'documentId', width: 90 },
  { label: '标题', prop: 'title', width: 220 },
  { label: '状态', prop: 'status', width: 100, scopedSlots: 'slotJobStatus' },
  { label: '阶段', prop: 'stage', width: 130 },
  { label: '进度', prop: 'progress', width: 180, scopedSlots: 'slotJobProgress' },
  { label: '切片数', prop: 'chunkCount', width: 90 },
  { label: '错误', prop: 'errorMessage', scopedSlots: 'slotJobError' },
  { label: '更新时间', prop: 'updatedAt', width: 170 },
]

const ragEditRef = ref()
const uploadDialog = ref({
  show: false,
  title: '',
  file: null,
  files: [],
  submitting: false,
})
const candidateDialog = ref({
  show: false,
  candidateId: null,
  question: '',
  answer: '',
  category: '',
  remark: '',
  submitting: false,
})

const clipText = (text, length = 100) => {
  if (!text) return '—'
  const value = String(text)
  return value.length > length ? `${value.slice(0, length)}…` : value
}

const parseSimilarQuestions = (value) => {
  if (Array.isArray(value)) return value
  if (!value) return []
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

const pageParams = (data, search) => ({
  pageNo: data.pageNo || 1,
  pageSize: data.pageSize || 15,
  ...Object.fromEntries(Object.entries(search).filter(([, value]) => value !== '')),
})

const loadFaqList = async () => {
  const result = await proxy.Request({
    url: proxy.Api.loadRagQuestion,
    params: pageParams(faqData.value, faqSearch.value),
  })
  if (!result) return
  result.data.list = (result.data.list || []).map((item) => ({
    ...item,
    similarQuestion: parseSimilarQuestions(item.similarQuestion),
  }))
  Object.assign(faqData.value, result.data)
}

const loadDocuments = async () => {
  const result = await proxy.Request({
    url: proxy.Api.knowledgeDocuments,
    params: pageParams(documentData.value, documentSearch.value),
  })
  if (!result) return
  Object.assign(documentData.value, result.data)
}

const loadCandidates = async () => {
  const result = await proxy.Request({
    url: proxy.Api.knowledgeFaqCandidates,
    params: pageParams(candidateData.value, candidateSearch.value),
  })
  if (!result) return
  Object.assign(candidateData.value, result.data)
}

const loadJobs = async () => {
  const result = await proxy.Request({
    url: proxy.Api.knowledgeJobs,
    params: pageParams(jobData.value, jobSearch.value),
  })
  if (!result) return
  Object.assign(jobData.value, result.data)
}

const onTabChange = (name) => {
  if (name === 'documents') loadDocuments()
  else if (name === 'candidates') loadCandidates()
  else if (name === 'jobs') loadJobs()
  else loadFaqList()
}

const showEdit = (data) => {
  ragEditRef.value.show(data)
}

const delRag = (data) => {
  proxy.Confirm({
    message: `确定删除 FAQ「${clipText(data.question, 30)}」吗？`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.delRagQuestion,
        params: { questionId: data.questionId },
      })
      if (!result) return
      proxy.Message.success('删除成功')
      loadFaqList()
    },
  })
}

const openUpload = () => {
  uploadDialog.value = {
    show: true,
    title: '',
    file: null,
    files: [],
    submitting: false,
  }
}

const onKnowledgeFileChange = (file, files) => {
  const error = validateKnowledgeFile(file?.raw)
  if (error) {
    proxy.Message.error(error)
    uploadDialog.value.file = null
    uploadDialog.value.files = []
    return
  }
  uploadDialog.value.file = file.raw
  uploadDialog.value.files = files.slice(-1)
}

const onKnowledgeFileRemove = () => {
  uploadDialog.value.file = null
  uploadDialog.value.files = []
}

const submitDocument = async () => {
  const validationError = validateKnowledgeFile(uploadDialog.value.file)
  if (validationError) {
    proxy.Message.error(validationError)
    return
  }
  uploadDialog.value.submitting = true
  try {
    const result = await proxy.Request({
      url: proxy.Api.knowledgeUpload,
      params: {
        file: uploadDialog.value.file,
        title: uploadDialog.value.title,
      },
      timeout: 60000,
    })
    if (!result) return
    proxy.Message.success('文档解析完成，发布后可用于问答')
    uploadDialog.value.show = false
    activeTab.value = 'documents'
    loadDocuments()
    loadJobs()
  } finally {
    uploadDialog.value.submitting = false
  }
}

const publishDocument = (row) => {
  proxy.Confirm({
    message: `确定发布知识文档「${row.title}」吗？`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.knowledgePublish,
        params: { documentId: row.documentId },
        timeout: 120000,
      })
      if (!result) return
      proxy.Message.success('知识文档已发布，缓存版本已刷新')
      loadDocuments()
      loadJobs()
    },
  })
}

const archiveDocument = (row) => {
  proxy.Confirm({
    message: `归档后该文档将不再参与检索，确定归档「${row.title}」吗？`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.knowledgeArchive,
        params: { documentId: row.documentId },
        timeout: 60000,
      })
      if (!result) return
      proxy.Message.success('知识文档已归档')
      loadDocuments()
    },
  })
}

const openCandidateReview = (row) => {
  candidateDialog.value = {
    show: true,
    candidateId: row.candidateId,
    question: row.question || '',
    answer: row.answer || '',
    category: row.category || 'general',
    remark: '',
    submitting: false,
  }
}

const approveCandidate = async () => {
  if (!candidateDialog.value.answer.trim()) {
    proxy.Message.error('标准答案不能为空')
    return
  }
  candidateDialog.value.submitting = true
  try {
    const result = await proxy.Request({
      url: proxy.Api.knowledgeReviewFaqCandidate,
      params: {
        candidateId: candidateDialog.value.candidateId,
        approved: true,
        correctedAnswer: candidateDialog.value.answer,
        category: candidateDialog.value.category,
        remark: candidateDialog.value.remark,
      },
      timeout: 60000,
    })
    if (!result) return
    proxy.Message.success('FAQ 候选已审核并发布')
    candidateDialog.value.show = false
    loadCandidates()
    loadFaqList()
  } finally {
    candidateDialog.value.submitting = false
  }
}

const rejectCandidate = async (row) => {
  try {
    const { value } = await ElMessageBox.prompt(
      '可填写拒绝原因，便于后续分析候选质量',
      '拒绝 FAQ 候选',
      {
        inputPlaceholder: '拒绝原因',
        confirmButtonText: '确认拒绝',
        cancelButtonText: '取消',
      }
    )
    const result = await proxy.Request({
      url: proxy.Api.knowledgeReviewFaqCandidate,
      params: {
        candidateId: row.candidateId,
        approved: false,
        remark: value,
      },
    })
    if (!result) return
    proxy.Message.success('FAQ 候选已拒绝')
    loadCandidates()
  } catch {
  }
}

const knowledgeStatusText = (status) => ({
  READY: '待发布',
  PUBLISHED: '已发布',
  DRAFT: '草稿',
  ERROR: '解析失败',
  ARCHIVED: '已归档',
}[status] || status || '未知')

const knowledgeStatusType = (status) => ({
  READY: 'warning',
  PUBLISHED: 'success',
  DRAFT: 'info',
  ERROR: 'danger',
  ARCHIVED: 'info',
}[status] || '')

const candidateStatusText = (status) => ({
  PENDING: '待审核',
  APPROVED: '已通过',
  REJECTED: '已拒绝',
}[status] || status || '未知')

const candidateStatusType = (status) => ({
  PENDING: 'warning',
  APPROVED: 'success',
  REJECTED: 'info',
}[status] || '')

const jobStatusText = (status) => ({
  RUNNING: '执行中',
  READY: '待发布',
  SUCCESS: '成功',
  FAILED: '失败',
}[status] || status || '未知')

const jobStatusType = (status) => ({
  RUNNING: 'primary',
  READY: 'warning',
  SUCCESS: 'success',
  FAILED: 'danger',
}[status] || '')
</script>

<style lang="scss" scoped>
.knowledge-page {
  height: 100%;
  min-height: 0;

  :deep(.el-tabs) {
    display: flex;
    height: 100%;
    flex-direction: column;
  }

  :deep(.el-tabs__content) {
    min-height: 0;
    flex: 1;
  }

  :deep(.el-tab-pane) {
    height: 100%;
  }
}

.tab-body {
  display: flex;
  height: 100%;
  min-height: 0;
  flex-direction: column;
}

.toolbar {
  flex: 0 0 auto;
  padding: 8px 0 2px;

  :deep(.el-form-item) {
    margin-bottom: 10px;
  }
}

.table-panel {
  min-height: 0;
  flex: 1;
}

.row-actions {
  display: flex;
  align-items: center;
  white-space: nowrap;
}

.error-text {
  color: var(--el-color-danger);
}

.upload-icon {
  margin-bottom: 8px;
  font-size: 42px;
  color: var(--el-color-primary);
}

.upload-tip {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
