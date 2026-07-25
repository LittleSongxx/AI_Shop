<template>
  <div class="m-simple">
    <section class="glass-card m-form">
      <h3 class="m-form-title">{{ editing ? '编辑敏感词' : '新增敏感词' }}</h3>
      <input v-model="form.word" class="m-input" placeholder="输入敏感词" maxlength="100" />
      <input v-model="form.replaceWord" class="m-input" placeholder="替换词(默认为***)" maxlength="100" />
      <div class="m-form-row">
        <label class="m-label">状态</label>
        <select v-model="form.status" class="m-input small">
          <option :value="1">启用</option>
          <option :value="0">停用</option>
        </select>
      </div>
      <div class="m-form-ops">
        <button type="button" class="op-btn primary" @click="save">{{ editing ? '更新' : '新增' }}</button>
        <button type="button" class="op-btn" @click="resetForm">重置</button>
        <button type="button" class="op-btn warning" @click="refreshCache">刷新缓存</button>
      </div>
    </section>

    <div v-if="list.length" class="m-list">
      <div v-for="row in list" :key="row.id" class="glass-card sensitive-row">
        <div class="sensitive-info">
          <span class="sensitive-word">{{ row.word }}</span>
          <span class="sensitive-meta">替换为 {{ row.replaceWord }} · {{ row.createTime }}</span>
        </div>
        <span class="m-tag" :class="row.status == 1 ? 'green' : 'muted'">{{ row.status == 1 ? '启用' : '停用' }}</span>
        <button type="button" class="sensitive-op" @click="editRow(row)">编辑</button>
        <button type="button" class="sensitive-op danger" @click="delRow(row)">删除</button>
      </div>
    </div>
    <p v-else class="m-empty-tip">暂无敏感词</p>
  </div>
</template>

<script setup>
import { ref, reactive, getCurrentInstance, onMounted } from 'vue'

const { proxy } = getCurrentInstance()
const list = ref([])
const editing = ref(false)
const form = reactive({ id: null, word: '', replaceWord: '', status: 1 })

const loadList = async () => {
  const result = await proxy.Request({ url: proxy.Api.sensitiveWordList, params: { pageNo: 1, pageSize: 100 }, showLoading: false })
  if (!result) return
  list.value = result.data?.list || []
}

const resetForm = () => {
  form.id = null
  form.word = ''
  form.replaceWord = ''
  form.status = 1
  editing.value = false
}

const editRow = (row) => {
  form.id = row.id
  form.word = row.word
  form.replaceWord = row.replaceWord
  form.status = row.status
  editing.value = true
}

const save = async () => {
  if (!form.word || !form.word.trim()) {
    proxy.Message.warning('请输入敏感词')
    return
  }
  const result = await proxy.Request({
    url: proxy.Api.sensitiveWordSave,
    params: {
      id: form.id,
      word: form.word.trim(),
      replaceWord: form.replaceWord?.trim() || '***',
      status: form.status
    },
    showLoading: true
  })
  if (!result) return
  proxy.Message.success('保存成功')
  resetForm()
  loadList()
}

const delRow = (row) => {
  proxy.Confirm({
    message: `确定删除敏感词「${row.word}」吗？`,
    okfun: async () => {
      const result = await proxy.Request({ url: proxy.Api.sensitiveWordDelete, params: { id: row.id } })
      if (!result) return
      proxy.Message.success('已删除')
      loadList()
    }
  })
}

const refreshCache = async () => {
  const result = await proxy.Request({
    url: proxy.Api.sensitiveWordRefresh,
    showLoading: true
  })
  if (!result) return
  proxy.Message.success('缓存已刷新')
}

onMounted(loadList)
</script>

<style lang="scss" scoped>
.m-simple {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.m-form {
  padding: 14px;

  .m-form-title {
    margin: 0 0 10px;
    font-size: 14px;
    font-weight: 600;
    color: var(--m-ink);
  }
}

.m-input {
  width: 100%;
  height: 40px;
  padding: 0 12px;
  border: 1px solid rgba(120, 120, 128, 0.24);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.6);
  font-size: 14px;
  color: var(--m-ink);
  outline: none;
  margin-bottom: 8px;

  &.small {
    width: 100px;
    height: 36px;
    margin-bottom: 0;
  }
}

.m-form-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;

  .m-label {
    font-size: 13px;
    color: var(--m-ink-2);
  }
}

.m-form-ops {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.op-btn {
  flex: 1;
  height: 38px;
  border: 1px solid rgba(120, 120, 128, 0.24);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.6);
  color: var(--m-ink-2);
  font-size: 13px;
  cursor: pointer;

  &.primary {
    background: var(--m-ink);
    border-color: var(--m-ink);
    color: #fff;
  }

  &.warning {
    background: rgba(255, 149, 0, 0.16);
    border-color: rgba(255, 149, 0, 0.32);
    color: #c75b00;
  }
}

.m-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sensitive-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;

  .sensitive-info {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;

    .sensitive-word {
      font-size: 14px;
      color: var(--m-ink);
    }

    .sensitive-meta {
      font-size: 11px;
      color: var(--m-ink-3);
    }
  }

  .sensitive-op {
    flex-shrink: 0;
    border: none;
    background: transparent;
    color: var(--m-ink-2);
    font-size: 12px;
    cursor: pointer;

    &.danger {
      color: var(--m-danger);
    }
  }
}

.m-tag {
  padding: 2px 8px;
  border-radius: 8px;
  font-size: 11px;

  &.green {
    background: rgba(52, 199, 89, 0.16);
    color: #1c8c3c;
  }

  &.muted {
    background: rgba(120, 120, 128, 0.16);
    color: var(--m-ink-2);
  }
}

.m-empty-tip {
  margin: 24px 0;
  text-align: center;
  font-size: 14px;
  color: var(--m-ink-3);
}
</style>
