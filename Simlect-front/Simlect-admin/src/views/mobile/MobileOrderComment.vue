<template>
  <div class="m-simple">
    <div class="m-search glass-card glass-strong">
      <input v-model="searchForm.nickNameFuzzy" class="search-input" placeholder="用户昵称" @keyup.enter="reload" />
      <input v-model="searchForm.productNameFuzzy" class="search-input" placeholder="商品名称" @keyup.enter="reload" />
    </div>

    <div v-if="list.length" class="m-list">
      <div v-for="row in list" :key="row.orderId" class="glass-card cmt-card" :class="{ 'cmt-deleted': row.status == 1 }">
        <div class="cmt-head">
          <Avatar :avatar="row.avatar || undefined" :width="36"></Avatar>
          <span class="cmt-nick">{{ row.nickName }}</span>
          <el-rate v-if="row.star" :model-value="row.star" disabled size="small" />
          <span v-if="row.status == 1" class="deleted-badge">已删除</span>
        </div>
        <p class="cmt-product">
          {{ row.productName }}
          <span v-if="row.orderItems && row.orderItems.length > 1" class="more-products-btn" @click.stop="showAllProducts(row)">等{{ row.orderItems.length }}件商品</span>
        </p>
        <p class="cmt-content">{{ row.commentContent }}</p>
        <div v-if="row.commentImages.length" class="cmt-imgs">
          <Cover v-for="(img, i) in row.commentImages" :key="i" :source="img" :width="56" border-radius="8px"></Cover>
        </div>
        <p v-if="row.recommentContent" class="cmt-recomment">
          <span class="tag">追评</span>{{ row.recommentContent }}
        </p>
        <div v-if="row.recommentImages.length" class="cmt-imgs">
          <Cover v-for="(img, i) in row.recommentImages" :key="i" :source="img" :width="56" border-radius="8px"></Cover>
        </div>
        <p v-if="row.commentBizReply" class="cmt-reply"><span class="tag">商家</span>{{ row.commentBizReply }}</p>
        <div v-if="row.status != 1" class="cmt-ops">
          <button type="button" class="op-btn" @click="reply(row)">商家回复</button>
          <button type="button" class="op-btn danger" @click="del(row)">删除</button>
        </div>
      </div>
    </div>
    <p v-else-if="!loading" class="m-empty-tip">暂无评价</p>

    <div ref="sentinel" class="m-sentinel">
      <span v-if="loading">加载中…</span>
      <span v-else-if="finished && list.length">没有更多了</span>
    </div>

    <CommentReply ref="commentRef" @reload="reload"></CommentReply>
  </div>
</template>

<script setup>
import CommentReply from '@/views/order/CommentReply.vue'
import { ref, reactive, getCurrentInstance, onMounted, onUnmounted } from 'vue'
import { ElMessageBox } from 'element-plus'

const { proxy } = getCurrentInstance()
const searchForm = reactive({ nickNameFuzzy: '', productNameFuzzy: '' })
const list = ref([])
const pageNo = ref(0)
const pageTotal = ref(1)
const loading = ref(false)
const finished = ref(false)
const sentinel = ref(null)
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
  try {
    const next = pageNo.value + 1
    const params = { pageNo: next, pageSize: 8 }
    if (searchForm.nickNameFuzzy) params.nickNameFuzzy = searchForm.nickNameFuzzy
    if (searchForm.productNameFuzzy) params.productNameFuzzy = searchForm.productNameFuzzy
    const result = await proxy.Request({ url: proxy.Api.loadComment, params, showLoading: false })
    if (!result) return
    const data = result.data || {}
    const chunk = (data.list || []).map((item) => ({
      ...item,
      commentImages: item.commentImages ? String(item.commentImages).split(',').filter(Boolean) : [],
      recommentImages: item.recommentImages ? String(item.recommentImages).split(',').filter(Boolean) : []
    }))
    list.value = next === 1 ? chunk : list.value.concat(chunk)
    pageNo.value = Number(data.pageNo) || next
    pageTotal.value = Number(data.pageTotal) || pageNo.value
    finished.value = pageNo.value >= pageTotal.value
  } finally {
    loading.value = false
    if (!finished.value && pageNo.value > 0 && sentinel.value) {
      setTimeout(() => {
        const rect = sentinel.value.getBoundingClientRect()
        const threshold = window.innerHeight + 300
        if (rect.bottom <= threshold) {
          loadList()
        }
      }, 200)
    }
  }
}

const reload = () => loadList(true)

const showAllProducts = (row) => {
  const items = row.orderItems || []
  let html = '<div style="max-height:400px;overflow-y:auto;">'
  items.forEach((item, idx) => {
    const cover = item.cover ? `<img src="${proxy.AppConfig.imageRootUrl}${item.cover.split(',')[0]}" style="width:60px;height:60px;object-fit:cover;border-radius:6px;flex-shrink:0;" />` : ''
    html += `<div style="display:flex;gap:12px;padding:10px 0;${idx > 0 ? 'border-top:1px solid #eee;' : ''}">
      ${cover}
      <div style="flex:1;min-width:0;">
        <div style="font-size:14px;font-weight:500;margin-bottom:4px;color:#1d1d1f;">${item.productName || ''}</div>
        <div style="font-size:12px;color:#86868b;">${item.propertyInfo || ''}</div>
        <div style="font-size:12px;color:#86868b;margin-top:2px;">￥${item.itemAmount || 0} × ${item.buyCount || 0}</div>
      </div>
    </div>`
  })
  html += '</div>'
  try {
    ElMessageBox.alert(html, '该订单商品', {
      dangerouslyUseHTMLString: true,
      confirmButtonText: '关闭',
      showCancelButton: false,
      closeOnClickModal: true,
    })
  } catch (e) {
    console.error(e)
  }
}

const commentRef = ref()
const reply = (row) => commentRef.value.show(row.orderId)

const del = (row) => {
  if (row.status == 1) {
    proxy.Message.warning('该评论已删除')
    return
  }
  proxy.Confirm({
    message: '确定要删除该评价吗？',
    okfun: async () => {
      const result = await proxy.Request({ url: proxy.Api.delComment, params: { orderId: row.orderId } })
      if (!result) return
      proxy.Message.success('评论已删除')
      reload()
    }
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
.m-simple {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.m-search {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 44px;
  padding: 0 12px;
  border-radius: 16px;

  .search-input {
    flex: 1;
    min-width: 0;
    border: none;
    background: transparent;
    font-size: 14px;
    color: var(--m-ink);
    outline: none;

    & + .search-input {
      border-left: 1px solid rgba(120, 120, 128, 0.2);
      padding-left: 8px;
    }

    &::placeholder {
      color: var(--m-ink-3);
    }
  }
}

.m-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.cmt-card {
  padding: 12px 14px;

  &.cmt-deleted {
    opacity: 0.6;
  }

  .cmt-head {
    display: flex;
    align-items: center;
    gap: 8px;

    .cmt-nick {
      font-size: 13px;
      font-weight: 600;
      color: var(--m-ink);
    }

    .deleted-badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 6px;
      background: var(--m-danger);
      color: #fff;
      font-size: 10px;
      font-weight: 600;
      margin-left: auto;
    }
  }

  .cmt-product {
    margin: 8px 0 4px;
    font-size: 12px;
    color: var(--m-ink-3);
    .more-products-btn {
      display: inline-block;
      margin-left: 6px;
      padding: 1px 6px;
      border-radius: 4px;
      background: var(--m-gold-soft);
      color: #927535;
      font-size: 10px;
      cursor: pointer;
      white-space: nowrap;
    }
  }

  .cmt-content {
    margin: 0 0 6px;
    font-size: 14px;
    color: var(--m-ink);
    line-height: 1.5;
  }

  .cmt-imgs {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 6px;
  }

  .cmt-recomment,
  .cmt-reply {
    margin: 6px 0 0;
    font-size: 13px;
    line-height: 1.45;
    color: var(--m-ink-2);

    .tag {
      display: inline-block;
      margin-right: 6px;
      padding: 0 6px;
      border-radius: 6px;
      background: var(--m-gold-soft);
      color: #927535;
      font-size: 11px;
    }
  }

  .cmt-reply {
    padding: 8px 10px;
    margin-top: 8px;
    border-radius: 10px;
    background: rgba(120, 120, 128, 0.08);
  }

  .cmt-ops {
    display: flex;
    gap: 8px;
    margin-top: 10px;

    .op-btn {
      flex: 1;
      height: 32px;
      border: 1px solid rgba(120, 120, 128, 0.24);
      border-radius: 10px;
      background: rgba(255, 255, 255, 0.5);
      color: var(--m-ink-2);
      font-size: 12px;
      cursor: pointer;

      &.danger {
        color: var(--m-danger);
        border-color: rgba(255, 59, 48, 0.3);
      }
    }
  }
}

.m-sentinel {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 28px;
  font-size: 12px;
  color: var(--m-ink-3);
}

.m-empty-tip {
  margin: 24px 0;
  text-align: center;
  font-size: 14px;
  color: var(--m-ink-3);
}
</style>
