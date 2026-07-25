<template>

  <div class="comment-panel">
    <div class="comment-title">
      <div class="title">初次评价</div>
      <div class="commend-time">{{data.commentTime}}</div>
      <div class="del" v-if="data.status==1">已删除</div>
    </div>
    <div class="comment-inner">
      <div class="comment-info">{{ data.commentContent }}</div>
      <div class="comment-images" v-if="data.commentImages?.length > 0">
        <div class="comment-image-item" v-for="(item, index) in data.commentImages">
          <Cover fit="cover" :source="item" :preImageList="data.commentImages"></Cover>
        </div>
      </div>
      <el-rate v-model="data.star" size="large" disabled />
    </div>
  </div>
  <div class="comment-panel" v-if="data.recommentContent">
    <div class="comment-title">
      <div class="title">追评</div>
      <div class="commend-time">{{data.recommentTime}}</div>
    </div>
    <div class="comment-inner">
      <div class="comment-info">{{ data.recommentContent }}</div>
      <div class="comment-images" v-if="data.recommentImages?.length > 0">
        <div class="comment-image-item" v-for="(item, index) in data.recommentImages">
          <Cover fit="cover" :source="item" :preImageList="data.recommentImages"></Cover>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, getCurrentInstance, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
const { proxy } = getCurrentInstance()
const router = useRouter()
const route = useRoute()

const props = defineProps({
  data: {
    type: Object,
    default: {},
  },
})
</script>

<style lang="scss" scoped>
.comment-panel {
  margin-bottom: 10px;
  border: 1px solid #ddd;
  border-radius: 5px;

  .comment-title {
    background: #ebebeb;
    padding: 5px;
    display: flex;
    .title {
      font-size: 13px;
      flex: 1;
    }
    .commend-time {
      font-size: 13px;
      margin-bottom: 3px;
      color: var(--text2);
    }
    .del {
      margin-left: 10px;
      color: red;
    }
  }

  .comment-inner {
    padding: 10px;
    .comment-info {
      margin-bottom: 5px;
    }
  }
}

.comment-images {
  display: flex;
  margin-top: 5px;

  .comment-image-item {
    margin-top: 5px;
    width: 72px;
    height: 72px;
    display: flex;
    align-items: center;
    position: relative;
    margin-right: 5px;

    .del {
      position: absolute;
      top: 0px;
      right: 0px;
      width: 20px;
      height: 20px;
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 0px 5px 0px 5px;
      cursor: pointer;
      color: #fff;
      background: var(--red);
    }
  }
}
</style>
