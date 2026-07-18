<template>
  <div class="logistics-panel">
    <el-form :model="formData" :rules="rules" ref="formDataRef" label-width="80px" @submit.prevent>
      <el-form-item label="发件人" prop="">
        <el-input clearable placeholder="请输入发件人" v-model.trim="formData.senderName"></el-input>
      </el-form-item>
      <el-form-item label="联系电话" prop="">
        <el-input clearable placeholder="请输入发货人联系电话" v-model.trim="formData.senderPhone" :maxlength="11"></el-input>
      </el-form-item>
      <el-form-item label="发货地址" prop="">
        <el-input type="textarea" clearable placeholder="请输入发货地址" v-model.trim="formData.senderAddress"></el-input>
      </el-form-item>

      <el-form-item label="" prop="">
        <el-button @click="saveSetting" type="primary">保存</el-button>
      </el-form-item>
    </el-form>
  </div>

</template>

<script setup>
import { ref, reactive, getCurrentInstance, nextTick, onMounted } from 'vue'
const { proxy } = getCurrentInstance()

const formData = ref({})
const formDataRef = ref()
const rules = {
  senderName: [{ required: true, message: '请输入发件人' }],
  senderPhone: [{ required: true, message: '请输入发件人' }],
  senderDddress: [{ required: true, message: '请输入发件人' }],
}

const getSysLogistic = async () => {
  let result = await proxy.Request({
    url: proxy.Api.getSysLogistics,
  })
  if (!result) {
    return
  }
  formData.value = result.data || {}
}

const saveSetting = async () => {
  let result = await proxy.Request({
    url: proxy.Api.saveSysSaveLogistics,
    params: formData.value,
  })
  if (!result) {
    return
  }
  proxy.Message.success('保存成功')
}

onMounted(() => {
  getSysLogistic()
})
</script>

<style lang="scss" scoped>
.logistics-panel {
  padding: 20px;
  width: 600px;
}
</style>
