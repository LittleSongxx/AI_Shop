<template>
  <div class="m-simple">
    <div class="m-search glass-card glass-strong">
      <input
        v-model="searchForm.questionFuzzy"
        class="search-input"
        placeholder="搜索问题"
        @keyup.enter="reload"
      />
      <button type="button" class="op-btn sm primary" @click="reload">搜索</button>
    </div>

    <button type="button" class="op-btn primary block" @click="showEdit()">新增问答</button>

    <div v-if="list.length" class="m-list">
      <div v-for="row in list" :key="row.questionId" class="glass-card rag-card">
        <p class="rag-question">{{ row.question }}</p>
        <div v-if="row.similarQuestion?.length" class="rag-similar">
          <span v-for="(item, index) in row.similarQuestion" :key="index" class="sim-item">
            {{ index + 1 }}. {{ item }}
          </span>
        </div>
        <p class="rag-time">{{ row.createTime }}</p>
        <div class="rag-ops">
          <button type="button" class="op-btn sm" @click="showEdit(row)">修改</button>
          <button type="button" class="op-btn sm danger" @click="delRag(row)">删除</button>
        </div>
      </div>
    </div>
    <p v-else-if="!loading" class="m-empty-tip">暂无问答</p>

    <div ref="sentinel" class="m-sentinel">
      <span v-if="loading">加载中…</span>
      <span v-else-if="finished && list.length">没有更多了</span>
    </div>

    <RagEdit ref="ragEditRef" @reload="reload" />
  </div>
</template>

<script setup>
import RagEdit from '@/views/setting/RagEdit.vue'
import { ref, reactive, getCurrentInstance, onMounted, onUnmounted } from 'vue'

const { proxy } = getCurrentInstance()
const searchForm = reactive({ questionFuzzy: '' })
const list = ref([])
const pageNo = ref(0)
const pageTotal = ref(1)
const loading = ref(false)
const finished = ref(false)
const sentinel = ref(null)
const ragEditRef = ref(null)
let observer = null

const loadList = async (reset = false) => {
  if (loading.value) return
  if (reset) {
    pageNo.value = 0
    pageTotal.value = 1
    finished.value = false
    list.value = []
  }
  if (finished.value) return
  loading.value = true
  pageNo.value += 1
  try {
    const result = await proxy.Request({
      url: proxy.Api.loadRagQuestion,
      params: {
        pageNo: pageNo.value,
        pageSize: 15,
        ...searchForm,
      },
    })
    if (!result) {
      pageNo.value -= 1
      return
    }
    const data = result.data || {}
    pageTotal.value = data.pageTotal || 1
    const rows = (data.list || []).map((item) => {
      if (item.similarQuestion && typeof item.similarQuestion === 'string') {
        try {
          item.similarQuestion = JSON.parse(item.similarQuestion)
        } catch {
          item.similarQuestion = []
        }
      }
      return item
    })
    list.value.push(...rows)
    if (pageNo.value >= pageTotal.value) finished.value = true
  } finally {
    loading.value = false
  }
}

const reload = () => loadList(true)

const showEdit = (data) => {
  ragEditRef.value.show(data)
}

const delRag = (data) => {
  proxy.Confirm({
    message: '确定要删除吗?',
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.delRagQuestion,
        params: { questionId: data.questionId },
      })
      if (!result) return
      proxy.Message.success('删除成功')
      reload()
    },
  })
}

onMounted(() => {
  loadList(true)
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) loadList()
    },
    { rootMargin: '0px 0px 300px 0px' }
  )
  if (sentinel.value) observer.observe(sentinel.value)
})

onUnmounted(() => {
  observer && observer.disconnect()
  observer = null
})
</script>

<style lang="scss" scoped>
.rag-card {
  padding: 12px 14px;
}

.rag-question {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--m-ink);
  line-height: 1.45;
}

.rag-similar {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;

  .sim-item {
    font-size: 12px;
    color: var(--m-ink-3);
    line-height: 1.4;
  }
}

.rag-time {
  margin: 0 0 10px;
  font-size: 11px;
  color: var(--m-ink-3);
}

.rag-ops {
  display: flex;
  gap: 8px;
}
</style>
