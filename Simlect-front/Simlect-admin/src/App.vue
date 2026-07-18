<template>
  <el-config-provider :locale="zhCn" :message="config">
    <WebSocketProvider />
    <router-view></router-view>
  </el-config-provider>
</template>

<script setup>
import { reactive, getCurrentInstance, onMounted } from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { useRouter, useRoute } from 'vue-router'
import { useDeviceStore } from '@/stores/device'
import WebSocketProvider from '@/components/WebSocketProvider.vue'
const { proxy } = getCurrentInstance()
const router = useRouter()
const route = useRoute()

const device = useDeviceStore()
device.bind()

const config = reactive({
  max: 1,
})
</script>

<style lang="scss" scoped>
</style>
