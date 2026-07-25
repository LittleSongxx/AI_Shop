<template>
  <div class="m-simple">
    <div v-if="promptList.length" class="m-chip-tabs">
      <button
        v-for="item in promptList"
        :key="item.key"
        type="button"
        class="chip"
        :class="{ active: activeName === item.key }"
        @click="switchPrompt(item.key)"
      >
        {{ item.desc }}
      </button>
    </div>

    <div class="glass-card m-form">
      <div class="m-field">
        <label class="m-label">提示词内容</label>
        <textarea
          v-model="formData.prompt"
          class="m-textarea prompt-area"
          placeholder="请输入提示词"
          rows="16"
        />
      </div>
      <div class="m-form-ops">
        <button type="button" class="op-btn primary" @click="savePrompt">保存</button>
        <button type="button" class="op-btn warning" @click="cleanPrompt">清空缓存</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, getCurrentInstance, onMounted } from 'vue'

const { proxy } = getCurrentInstance()
const activeName = ref('')
const formData = ref({ prompt: '' })
const promptList = ref([])

const getPromptDetail = async () => {
  if (!activeName.value) return
  const result = await proxy.Request({
    url: proxy.Api.getPromptDetail,
    params: { key: activeName.value },
  })
  if (!result) return
  formData.value.prompt = result.data || ''
}

const switchPrompt = async (key) => {
  activeName.value = key
  await getPromptDetail()
}

const loadPrompt = async () => {
  const result = await proxy.Request({ url: proxy.Api.loadPromptList })
  if (!result) return
  promptList.value = result.data || []
  if (promptList.value.length) {
    activeName.value = promptList.value[0].key
    await getPromptDetail()
  }
}

const savePrompt = async () => {
  if (!formData.value.prompt?.trim()) {
    proxy.Message.warning('请输入提示词')
    return
  }
  const result = await proxy.Request({
    url: proxy.Api.savePrompt,
    params: { key: activeName.value, prompt: formData.value.prompt },
    showLoading: true,
  })
  if (!result) return
  proxy.Message.success('保存成功')
}

const cleanPrompt = () => {
  proxy.Confirm({
    message: '确定要清空缓存吗？清空后将使用系统默认提示词',
    okfun: async () => {
      const result = await proxy.Request({
        url: proxy.Api.cleanPromptCache,
        params: { key: activeName.value },
        showLoading: true,
      })
      if (!result) return
      proxy.Message.success('清空缓存成功')
      getPromptDetail()
    },
  })
}

onMounted(loadPrompt)
</script>

<style lang="scss" scoped>
.prompt-area {
  min-height: 280px;
}
</style>
