<template>
  <Drawer :show="dialogConfig.show" :title="dialogConfig.title" :buttons="dialogConfig.buttons" width="80%"
    @close="dialogConfig.show = false">
    <el-form :model="formData" :rules="rules" ref="formDataRef" label-width="70px" @submit.prevent>
      <el-form-item label="问题" prop="question">
        <el-input clearable placeholder="请输入问题" v-model.trim="formData.question"></el-input>
      </el-form-item>
      <el-form-item label="相似问题" prop="similarQuestion">
        <div class="similar-questions-panel">
          <el-button @click="addSimilarQuestion" type="primary">增加相似问题</el-button>
          <div class="similar-questions-list">
            <div class="similar-question-item" v-for="(question, index) in formData.similarQuestion" :key="index">
              <el-input v-model="formData.similarQuestion[index]" placeholder="请输入相似问题" clearable>
              </el-input>
              <div class="iconfont icon-delete" @click="delSimilarQuestion(index)"></div>
            </div>
          </div>
        </div>
      </el-form-item>
      <el-form-item label="答案" prop="answer">
        <div class="editor-panel">
          <EditorMarkdown v-model="formData.answer"></EditorMarkdown>
        </div>
      </el-form-item>
    </el-form>
  </Drawer>
</template>

<script setup>
import EditorMarkdown from '@/components/markdown/EditorMarkdown.vue'
import { ref, reactive, getCurrentInstance, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
const { proxy } = getCurrentInstance()
const router = useRouter()
const route = useRoute()

const dialogConfig = ref({
  show: false,
  title: '编辑问题',
  buttons: [
    {
      type: 'primary',
      text: '保存',
      click: (e) => {
        sumitForm()
      },
    },
  ],
})

const show = async (data = {}) => {
  dialogConfig.value.show = true
  await nextTick()
  formDataRef.value.resetFields()
  formData.value = { ...data }
  if (!formData.value.similarQuestion) {
    formData.value.similarQuestion = []
  }
}

defineExpose({
  show,
})

const formData = ref({
  similarQuestion: [],
})

const formDataRef = ref()
const rules = {
  question: [{ required: true, message: '请输入问题', trigger: 'blur' }],
  answer: [{ required: true, message: '请输入答案', trigger: 'blur' }],
  similarQuestion: [
    {
      validator: (rule, value, callback) => {
        if (value.length == 0) {
          callback()
          return
        }
        const empty = value.find((item) => {
          return item.trim() === ''
        })
        if (empty != null) {
          callback(new Error('相似问题不能为空'))
          return
        }
        const uniqueQuestions = [...new Set(value)]
        if (uniqueQuestions.length !== value.length) {
          callback(new Error('存在重复的相似问题'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
}


const addSimilarQuestion = () => {
  formData.value.similarQuestion.push('')
}


const delSimilarQuestion = (index) => {
  formData.value.similarQuestion.splice(index, 1)
}

const emit = defineEmits(['reload'])
const sumitForm = () => {
  formDataRef.value.validate(async (valid) => {
    if (!valid) {
      return
    }
    let params = {}
    Object.assign(params, formData.value)
    if (params.similarQuestion.length > 0) {
      params.similarQuestion = JSON.stringify(params.similarQuestion)
    } else {
      delete params.similarQuestion
    }
    let result = await proxy.Request({
      url: proxy.Api.saveRagQuestion,
      params,
    })
    if (!result) {
      return
    }
    dialogConfig.value.show = false
    proxy.Message.success('保存成功')
    emit('reload')
    return
  })
}
</script>

<style lang="scss" scoped>
.similar-questions-panel {
  width: 100%;

  .similar-questions-list {
    max-height: 158px;
    overflow: auto;
    padding: 5px 10px 5px 0px;

    &::-webkit-scrollbar {
      width: 4px;
      height: 4px;
    }

    .similar-question-item {
      display: flex;
      margin-bottom: 8px;

      .icon-delete {
        cursor: pointer;
        margin-left: 10px;
      }
    }
  }
}

.editor-panel {
  height: calc(100vh - 390px);
  width: 100%;
}
</style>
