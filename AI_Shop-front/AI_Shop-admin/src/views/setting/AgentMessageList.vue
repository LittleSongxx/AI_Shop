<template>
  <el-card class="ai-service-card">
    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <el-tab-pane label="对话记录" name="messages">
        <div class="search-panel">
          <el-form :model="searchForm" @submit.prevent>
            <el-row :gutter="10">
              <el-col :span="5">
                <el-form-item label="用户ID">
                  <el-input v-model="searchForm.userId" clearable placeholder="用户ID" />
                </el-form-item>
              </el-col>
              <el-col :span="5">
                <el-form-item label="业务类型">
                  <el-input v-model="searchForm.bizType" clearable placeholder="如 product_search" />
                </el-form-item>
              </el-col>
              <el-col :span="5">
                <el-button type="primary" @click="loadMessageList">搜索</el-button>
              </el-col>
            </el-row>
          </el-form>
        </div>
        <div class="table-panel">
          <Table ref="tableRef" :columns="messageColumns" :fetch="loadMessageList" :dataSource="tableData">
            <template #slotStatus="{ row }">
              <el-tag v-if="row.status === 0" type="info">已取消</el-tag>
              <el-tag v-else-if="row.status === 1" type="warning">回答中</el-tag>
              <el-tag v-else-if="row.status === 3" type="info">已中断</el-tag>
              <el-tag v-else type="success">完成</el-tag>
            </template>
            <template #slotUser="{ row }">
              <p class="msg-line user">{{ row.userMessage || '—' }}</p>
            </template>
            <template #slotAi="{ row }">
              <p class="msg-line ai">{{ clipText(row.assistantMessage) }}</p>
            </template>
            <template #slotOp="{ row }">
              <div class="list-op-panel">
                <OpBtn icon="icon-delete" type="danger" tips="删除" @click="delRow(row)" />
              </div>
            </template>
          </Table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="人工会话" name="support">
        <div v-if="supportStats" class="support-stats">
          <el-tag type="info">近{{ supportStats.windowHours }}小时 {{ supportStats.totalSessions }}个会话</el-tag>
          <el-tag type="success">首次响应达标 {{ formatRate(supportStats.firstResponseSlaRate) }}</el-tag>
          <el-tag type="warning">超时排队 {{ supportStats.overdueQueued }}</el-tag>
          <el-tag type="danger">待响应超时 {{ supportStats.overdueFirstResponse }}</el-tag>
          <span class="support-stat-text">
            平均排队 {{ formatSeconds(supportStats.averageQueueWaitSeconds) }}，
            平均首次响应 {{ formatSeconds(supportStats.averageFirstResponseSeconds) }}
          </span>
        </div>
        <div class="search-panel">
          <el-form :model="supportForm" @submit.prevent>
            <el-row :gutter="10">
              <el-col :span="4">
                <el-form-item label="状态">
                  <el-select v-model="supportForm.status" clearable>
                    <el-option label="排队中" value="QUEUED" />
                    <el-option label="已认领" value="ASSIGNED" />
                    <el-option label="处理中" value="ACTIVE" />
                    <el-option label="已解决" value="RESOLVED" />
                    <el-option label="已取消" value="CANCELLED" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="5">
                <el-form-item label="用户ID">
                  <el-input v-model="supportForm.userId" clearable placeholder="用户ID" />
                </el-form-item>
              </el-col>
              <el-col :span="5">
                <el-button type="primary" @click="loadSupportSessions">刷新</el-button>
              </el-col>
            </el-row>
          </el-form>
        </div>
        <div class="table-panel">
          <Table :columns="supportColumns" :fetch="loadSupportSessions" :dataSource="supportData">
            <template #slotSupportStatus="{ row }">
              <el-tag :type="supportStatusType(row.status)">{{ supportStatusText(row.status) }}</el-tag>
            </template>
            <template #slotSummary="{ row }">
              <p class="msg-line ai">{{ clipText(row.summary, 180) }}</p>
            </template>
            <template #slotSupportOp="{ row }">
              <div class="support-op">
                <el-button size="small" @click="openSupport(row)">查看/回复</el-button>
                <el-button v-if="canClaimSupport(row.status)" size="small" type="primary" @click="claimSession(row)">认领</el-button>
                <el-button v-if="canActivateSupport(row.status)" size="small" type="success" @click="activateSession(row)">接入</el-button>
                <el-button v-if="canHandleSupport(row.status)" size="small" type="success" @click="resolveSession(row)">解决</el-button>
                <el-button v-if="canHandleSupport(row.status)" size="small" @click="returnAi(row)">转回AI</el-button>
              </div>
            </template>
          </Table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="坏例反馈" name="badcases">
        <div class="search-panel">
          <el-form :model="badcaseForm" @submit.prevent>
            <el-row :gutter="10">
              <el-col :span="4">
                <el-form-item label="状态">
                  <el-select v-model="badcaseForm.status" clearable>
                    <el-option label="待处理" value="PENDING" />
                    <el-option label="已处理" value="RESOLVED" />
                    <el-option label="忽略" value="IGNORED" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="5">
                <el-button type="primary" @click="loadBadcases">刷新</el-button>
              </el-col>
            </el-row>
          </el-form>
        </div>
        <div class="table-panel">
          <Table :columns="badcaseColumns" :fetch="loadBadcases" :dataSource="badcaseData">
            <template #slotBadcaseUser="{ row }">
              <p class="msg-line user">{{ clipText(row.userMessage, 160) }}</p>
            </template>
            <template #slotBadcaseAi="{ row }">
              <p class="msg-line ai">{{ clipText(row.assistantMessage, 180) }}</p>
            </template>
            <template #slotBadcaseOp="{ row }">
              <div class="support-op">
                <el-button
                  v-if="row.status === 'PENDING'"
                  size="small"
                  type="primary"
                  @click="promoteBadcase(row)"
                >
                  转FAQ
                </el-button>
                <el-button
                  v-if="row.status === 'PENDING'"
                  size="small"
                  @click="ignoreBadcase(row)"
                >
                  忽略
                </el-button>
              </div>
            </template>
          </Table>
        </div>
      </el-tab-pane>
    </el-tabs>
  </el-card>

  <el-drawer v-model="supportDrawer.show" :title="supportDrawer.title" size="520px">
    <div class="support-session-panel">
      <div class="support-history">
        <div
          v-for="item in supportDrawer.history"
          :key="item.supportMessageId"
          class="support-msg"
          :class="String(item.senderType || '').toLowerCase()"
        >
          <div class="support-msg-head">
            <span>{{ senderText(item.senderType) }}</span>
            <span>{{ item.createdAt }}</span>
          </div>
          <p>{{ item.content }}</p>
        </div>
        <p v-if="!supportDrawer.history.length" class="empty-tip">暂无会话记录</p>
      </div>
      <el-input
        v-model="supportDrawer.reply"
        type="textarea"
        :rows="4"
        maxlength="500"
        show-word-limit
        placeholder="输入人工客服回复"
      />
      <div class="drawer-actions">
        <el-button @click="supportDrawer.show = false">关闭</el-button>
        <el-button type="primary" :disabled="!supportDrawer.reply.trim()" @click="replySupport">发送回复</el-button>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { ElMessageBox } from 'element-plus'
import { getCurrentInstance, ref } from 'vue'

import {
  canActivateSupport,
  canClaimSupport,
  canHandleSupport,
  supportStatusText,
  supportStatusType,
} from '@/utils/supportStatus.js'

const { proxy } = getCurrentInstance()
const activeTab = ref('messages')
const tableRef = ref()
const tableData = ref({})
const supportData = ref({})
const supportStats = ref(null)
const badcaseData = ref({})
const searchForm = ref({ userId: '', bizType: '' })
const supportForm = ref({ status: 'QUEUED', userId: '' })
const badcaseForm = ref({ status: 'PENDING' })
const supportDrawer = ref({
  show: false,
  title: '人工会话',
  sessionId: '',
  reply: '',
  history: [],
})

const clipText = (text, len = 120) => {
  if (!text) return '—'
  const s = String(text)
  return s.length > len ? `${s.slice(0, len)}…` : s
}

const messageColumns = [
  { label: 'ID', prop: 'messageId', width: 80 },
  { label: '用户ID', prop: 'userId', width: 120 },
  { label: '状态', prop: 'status', width: 90, scopedSlots: 'slotStatus' },
  { label: '业务类型', prop: 'bizType', width: 140 },
  { label: '用户消息', prop: 'userMessage', scopedSlots: 'slotUser' },
  { label: 'AI回复', prop: 'assistantMessage', scopedSlots: 'slotAi' },
  { label: '时间', prop: 'sendTime', width: 170 },
  { label: '操作', prop: 'op', width: 80, scopedSlots: 'slotOp' },
]

const supportColumns = [
  { label: '会话ID', prop: 'sessionId', width: 270 },
  { label: '用户ID', prop: 'userId', width: 120 },
  { label: '状态', prop: 'status', width: 90, scopedSlots: 'slotSupportStatus' },
  { label: '意图', prop: 'intent', width: 130 },
  { label: '情绪', prop: 'sentiment', width: 100 },
  { label: '紧急度', prop: 'urgency', width: 100 },
  { label: '摘要', prop: 'summary', scopedSlots: 'slotSummary' },
  { label: '创建时间', prop: 'createdAt', width: 170 },
  { label: '操作', prop: 'op', width: 300, scopedSlots: 'slotSupportOp' },
]

const badcaseColumns = [
  { label: 'ID', prop: 'candidateId', width: 90 },
  { label: '类型', prop: 'candidateType', width: 150 },
  { label: '原因', prop: 'reason', width: 180 },
  { label: '状态', prop: 'status', width: 100 },
  { label: '用户消息', prop: 'userMessage', scopedSlots: 'slotBadcaseUser' },
  { label: 'AI回复', prop: 'assistantMessage', scopedSlots: 'slotBadcaseAi' },
  { label: '创建时间', prop: 'createdAt', width: 170 },
  { label: '操作', prop: 'op', width: 140, scopedSlots: 'slotBadcaseOp' },
]

const onTabChange = (tabName) => {
  if (tabName === 'support') {
    loadSupportSessions()
    loadSupportStats()
  } else if (tabName === 'badcases') {
    loadBadcases()
  } else {
    loadMessageList()
  }
}

const loadMessageList = async () => {
  const params = {
    pageNo: tableData.value.pageNo,
    pageSize: tableData.value.pageSize,
  }
  if (searchForm.value.userId) params.userId = searchForm.value.userId
  if (searchForm.value.bizType) params.bizType = searchForm.value.bizType
  const result = await proxy.Request({
    url: proxy.Api.agentMessageLoadList,
    params,
  })
  if (!result) return
  Object.assign(tableData.value, result.data)
}

const loadSupportSessions = async () => {
  const params = {
    pageNo: supportData.value.pageNo,
    pageSize: supportData.value.pageSize,
  }
  if (supportForm.value.status) params.status = supportForm.value.status
  if (supportForm.value.userId) params.userId = supportForm.value.userId
  const result = await proxy.Request({
    url: proxy.Api.agentSupportSessions,
    params,
  })
  if (!result) return
  Object.assign(supportData.value, result.data)
}

const loadSupportStats = async () => {
  const result = await proxy.Request({
    url: proxy.Api.agentSupportStats,
    params: { windowHours: 24 },
    showLoading: false,
  })
  if (result) supportStats.value = result.data || null
}

const loadBadcases = async () => {
  const params = {
    pageNo: badcaseData.value.pageNo,
    pageSize: badcaseData.value.pageSize,
  }
  if (badcaseForm.value.status) params.status = badcaseForm.value.status
  const result = await proxy.Request({
    url: proxy.Api.agentBadcases,
    params,
  })
  if (!result) return
  Object.assign(badcaseData.value, result.data)
}

const callSupport = async (url, params, successText) => {
  const result = await proxy.Request({ url, params, showLoading: true })
  if (!result) return null
  if (successText) proxy.Message.success(successText)
  loadSupportSessions()
  loadSupportStats()
  return result.data
}

const claimSession = (row) => callSupport(proxy.Api.agentSupportClaim, { sessionId: row.sessionId }, '已认领')
const activateSession = (row) => callSupport(proxy.Api.agentSupportActivate, { sessionId: row.sessionId }, '已接入')

const resolveSession = async (row) => {
  try {
    const { value } = await ElMessageBox.prompt('可填写处理备注', '解决人工会话', {
      inputPlaceholder: '处理备注，可留空',
      confirmButtonText: '标记解决',
      cancelButtonText: '取消',
    })
    await callSupport(proxy.Api.agentSupportResolve, { sessionId: row.sessionId, remark: value }, '已解决')
  } catch {
  }
}

const returnAi = (row) => {
  proxy.Confirm({
    message: '确定将该会话转回AI客服吗？',
    okfun: () => callSupport(proxy.Api.agentSupportReturnAi, { sessionId: row.sessionId }, '已转回AI'),
  })
}

const openSupport = async (row) => {
  supportDrawer.value.show = true
  supportDrawer.value.title = `人工会话 ${row.sessionId}`
  supportDrawer.value.sessionId = row.sessionId
  supportDrawer.value.reply = ''
  await loadSupportHistory()
}

const loadSupportHistory = async () => {
  const result = await proxy.Request({
    url: proxy.Api.agentSupportHistory,
    params: { sessionId: supportDrawer.value.sessionId, limit: 200 },
    showLoading: false,
  })
  if (!result) return
  supportDrawer.value.history = result.data || []
}

const replySupport = async () => {
  const content = supportDrawer.value.reply.trim()
  if (!content) return
  const data = await callSupport(
    proxy.Api.agentSupportReply,
    { sessionId: supportDrawer.value.sessionId, content },
    '已发送'
  )
  if (!data) return
  supportDrawer.value.reply = ''
  await loadSupportHistory()
}

const promoteBadcase = async (row) => {
  try {
    const { value: faqAnswer } = await ElMessageBox.prompt(
      '请输入人工修正后的标准答案，提交后会进入 FAQ 候选池',
      '转入 FAQ',
      {
        inputType: 'textarea',
        inputPlaceholder: '标准答案',
        confirmButtonText: '提交',
        cancelButtonText: '取消',
      }
    )
    const result = await proxy.Request({
      url: proxy.Api.agentReviewBadcase,
      params: {
        candidateId: row.candidateId,
        status: 'RESOLVED',
        faqAnswer,
        remark: '已根据线上 badcase 修正并提交 FAQ 候选',
      },
      showLoading: true,
    })
    if (!result) return
    proxy.Message.success('已处理并提交 FAQ 候选')
    loadBadcases()
  } catch {
  }
}

const ignoreBadcase = (row) => {
  proxy.Confirm({
    message: '确定忽略该坏例吗？',
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.agentReviewBadcase,
        params: {
          candidateId: row.candidateId,
          status: 'IGNORED',
          remark: '运营确认暂不处理',
        },
        showLoading: true,
      })
      if (!result) return
      proxy.Message.success('已忽略')
      loadBadcases()
    },
  })
}

const formatRate = (value) => `${(Number(value || 0) * 100).toFixed(1)}%`
const formatSeconds = (value) => {
  const seconds = Math.max(0, Number(value || 0))
  if (seconds < 60) return `${seconds.toFixed(0)}秒`
  return `${(seconds / 60).toFixed(1)}分钟`
}
const senderText = (sender) => ({
  USER: '用户',
  ADMIN: '人工客服',
  SYSTEM: '系统',
  AI: 'AI',
}[sender] || sender || '未知')

const delRow = (row) => {
  proxy.Confirm({
    message: `确定删除对话记录 #${row.messageId} 吗？`,
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.agentMessageDelete,
        params: { messageId: row.messageId },
        showLoading: true,
      })
      if (!result) return
      proxy.Message.success('已删除')
      loadMessageList()
    },
  })
}
</script>

<style scoped lang="scss">
.ai-service-card {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;

  :deep(.el-card__body) {
    height: 100%;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
  }

  :deep(.el-tabs) {
    height: 100%;
    display: flex;
    flex-direction: column;
  }

  :deep(.el-tabs__content) {
    flex: 1;
    min-height: 0;
  }

  :deep(.el-tab-pane) {
    height: 100%;
    display: flex;
    flex-direction: column;
  }
}

.support-stats {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.support-stat-text {
  color: #606266;
  font-size: 13px;
}

.table-panel {
  flex: 1;
  min-height: 0;
}

.msg-line {
  margin: 0;
  font-size: 13px;
  line-height: 1.45;
  text-align: left;
  word-break: break-word;

  &.user {
    color: #333;
  }

  &.ai {
    color: #666;
  }
}

.list-op-panel,
.support-op {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
}

.support-session-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.support-history {
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-right: 4px;
}

.support-msg {
  padding: 10px 12px;
  border-radius: 8px;
  background: #f6f7fb;

  &.admin {
    background: #eef7ff;
  }

  &.user {
    background: #fff7e8;
  }

  p {
    margin: 6px 0 0;
    color: #333;
    line-height: 1.5;
    word-break: break-word;
  }
}

.support-msg-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: #888;
}

.drawer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.empty-tip {
  color: #999;
  text-align: center;
}
</style>
