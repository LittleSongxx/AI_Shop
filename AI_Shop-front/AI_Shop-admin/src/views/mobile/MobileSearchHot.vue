<template>
  <div class="m-simple">
    <section class="glass-card m-form">
      <h3 class="m-form-title">{{ editing ? '编辑热搜词' : '新增热搜词' }}</h3>
      <input v-model="form.keyword" class="m-input" placeholder="输入热搜词" maxlength="100" />
      <div class="m-form-row">
        <label class="m-label">排序</label>
        <input v-model.number="form.sort" class="m-input small" type="number" min="0" />
        <label class="m-label">状态</label>
        <select v-model="form.status" class="m-input small">
          <option :value="1">启用</option>
          <option :value="0">停用</option>
        </select>
      </div>
      <div class="m-form-ops">
        <button type="button" class="op-btn primary" @click="save">保存</button>
        <button type="button" class="op-btn" @click="resetForm">重置</button>
      </div>
    </section>

    <div v-if="list.length" class="m-list">
      <div v-for="row in list" :key="row.keyword" class="glass-card hot-row">
        <div class="hot-info">
          <span class="hot-word">{{ row.keyword }}</span>
          <span class="hot-meta">排序 {{ row.sort }} · {{ row.updateTime }}</span>
        </div>
        <span class="m-tag" :class="row.status == 1 ? 'green' : 'muted'">{{ row.status == 1 ? '启用' : '停用' }}</span>
        <button type="button" class="hot-op" @click="editRow(row)">编辑</button>
        <button type="button" class="hot-op danger" @click="delRow(row)">删除</button>
      </div>
    </div>
    <p v-else class="m-empty-tip">暂无热搜词</p>
  </div>
</template>

<script setup>
import { ref, reactive, getCurrentInstance, onMounted } from 'vue'

const { proxy } = getCurrentInstance()
const list = ref([])
const editing = ref(false)
const form = reactive({ keyword: '', sort: 0, status: 1 })

const loadList = async () => {
  const result = await proxy.Request({ url: proxy.Api.searchHotKeywordLoadList, showLoading: false })
  if (!result) return
  list.value = result.data || []
}

const resetForm = () => {
  form.keyword = ''
  form.sort = 0
  form.status = 1
  editing.value = false
}

const editRow = (row) => {
  form.keyword = row.keyword
  form.sort = row.sort ?? 0
  form.status = row.status ?? 1
  editing.value = true
}

const save = async () => {
  if (!form.keyword || !form.keyword.trim()) {
    proxy.Message.warning('请输入热搜词')
    return
  }
  const result = await proxy.Request({
    url: proxy.Api.searchHotKeywordSave,
    params: { keyword: form.keyword.trim(), sort: form.sort, status: form.status },
    showLoading: true
  })
  if (!result) return
  proxy.Message.success('保存成功')
  resetForm()
  loadList()
}

const delRow = (row) => {
  proxy.Confirm({
    message: `确定删除热搜词「${row.keyword}」吗？`,
    okfun: async () => {
      const result = await proxy.Request({ url: proxy.Api.searchHotKeywordDel, params: { keyword: row.keyword } })
      if (!result) return
      proxy.Message.success('已删除')
      loadList()
    }
  })
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

  &.small {
    width: 84px;
    height: 36px;
  }
}

.m-form-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;

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
  font-size: 14px;
  cursor: pointer;

  &.primary {
    background: var(--m-ink);
    border-color: var(--m-ink);
    color: #fff;
  }
}

.m-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.hot-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;

  .hot-info {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;

    .hot-word {
      font-size: 14px;
      color: var(--m-ink);
    }

    .hot-meta {
      font-size: 11px;
      color: var(--m-ink-3);
    }
  }

  .hot-op {
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
