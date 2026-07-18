<template>
  <el-dialog v-model="visible" title="评价订单" width="92%" :style="{ maxWidth: '480px' }" destroy-on-close @closed="reset">
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
      <el-form-item label="星级" prop="star">
        <el-rate v-model="form.star" size="large" />
      </el-form-item>
      <el-form-item label="评价内容" prop="commentContent">
        <el-input
          v-model="form.commentContent"
          type="textarea"
          :rows="4"
          maxlength="300"
          show-word-limit
          placeholder="分享你的购物体验吧"
        />
      </el-form-item>
      <el-form-item label="评价图片（选填，最多5张）">
        <div v-if="imageList.length" class="img-list">
          <div v-for="(img, idx) in imageList" :key="idx" class="img-item">
            <img :src="toImageSrc(img)" alt="" />
            <button type="button" class="img-del" aria-label="删除" @click="removeImage(idx)">×</button>
          </div>
        </div>
        <el-upload
          v-if="imageList.length < 5"
          class="upload-trigger"
          :show-file-list="false"
          accept="image/*"
          :http-request="onUpload"
        >
          <el-button plain type="button">{{ imageList.length ? '继续添加' : '添加图片' }}</el-button>
        </el-upload>
      </el-form-item>
    </el-form>
    <template #footer>
      <div class="footer-row">
        <div class="footer-actions">
          <el-button @click="visible = false">取消</el-button>
          <el-button type="primary" :loading="submitting" @click="submit">提交评价</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
  <ImageEditorDialog ref="imageEditorRef" />
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue';
import type { FormInstance, FormRules, UploadRequestOptions } from 'element-plus';
import { commentApi, fileApi } from '@/api/modules';
import ImageEditorDialog from '@/components/business/ImageEditorDialog.vue';
import { normalizeCommentImagePath, serializeCommentImagePaths } from '@/utils/commentImagePaths';
import { resolveCommentUploadBlob } from '@/utils/imageUpload';
import { resolveImageUrl } from '@/utils/image';
import { formatUploadErrorMessage } from '@/utils/apiError';
import { toast } from '@/utils/toast';

const emit = defineEmits<{ success: [] }>();

const visible = ref(false);
const submitting = ref(false);
const formRef = ref<FormInstance>();
const imageEditorRef = ref<InstanceType<typeof ImageEditorDialog> | null>(null);
const imageList = ref<string[]>([]);

const form = reactive({
  orderId: '',
  commentContent: '',
  star: 5
});

const rules: FormRules = {
  commentContent: [{ required: true, message: '请输入评价内容', trigger: 'blur' }],
  star: [{ required: true, message: '请选择星级', trigger: 'change' }]
};

const toImageSrc = (path: unknown) => {
  const normalized = normalizeCommentImagePath(path);
  return normalized ? resolveImageUrl(normalized, { useThumbnail: false }) : '';
};

const reset = () => {
  form.orderId = '';
  form.commentContent = '';
  form.star = 5;
  imageList.value = [];
  formRef.value?.clearValidate();
};

const show = (orderId: string) => {
  reset();
  form.orderId = orderId;
  visible.value = true;
};

const onUpload = async (options: UploadRequestOptions) => {
  try {
    const file = options.file as File;
    const blob = await resolveCommentUploadBlob(file, (f) => imageEditorRef.value!.open(f));
    const uploaded = await fileApi.uploadImage(blob, true, 'comment', form.orderId, { skipPrepare: true });
    const path = normalizeCommentImagePath(uploaded);
    if (path) {
      imageList.value.push(path);
      options.onSuccess?.(path);
      if (uploaded.pendingReview) {
        toast.warning('该图片存在违规风险，已提交人工审核，提交评价后将锁定订单直至审核完成');
      }
    } else {
      options.onError?.(new Error('empty') as any);
      toast.error('图片上传失败');
    }
  } catch (e: any) {
    options.onError?.(e as any);
    toast.error(formatUploadErrorMessage(e));
  }
};

const removeImage = (idx: number) => {
  imageList.value.splice(idx, 1);
};

const submit = async () => {
  const valid = await formRef.value?.validate().catch(() => false);
  if (!valid) return;

  submitting.value = true;
  try {
    const result = await commentApi.postComment({
      orderId: form.orderId,
      commentContent: form.commentContent.trim(),
      star: form.star,
      commentImages: serializeCommentImagePaths(imageList.value) || undefined
    }) as { pendingReview?: boolean };
    if (result?.pendingReview) {
      toast.success('评价已提交，图片审核通过后将自动展示');
    } else {
      toast.success('评价成功');
    }
    visible.value = false;
    emit('success');
  } finally {
    submitting.value = false;
  }
};

defineExpose({ show });
</script>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.img-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.img-item {
  position: relative;
  width: 72px;
  height: 72px;
  border-radius: $radius-xs;
  overflow: hidden;

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .img-del {
    position: absolute;
    top: 0;
    right: 0;
    width: 22px;
    height: 22px;
    border: none;
    border-radius: 0 0 0 6px;
    background: rgba(0, 0, 0, 0.55);
    color: #fff;
    cursor: pointer;
    font-size: 16px;
    line-height: 1;
  }
}

.footer-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
}

.footer-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
</style>
