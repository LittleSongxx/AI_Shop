<template>
  <el-dialog v-model="visible" title="追评" width="92%" :style="{ maxWidth: '480px' }" destroy-on-close @closed="reset">
    <section v-if="initial.commentContent" class="initial-comment">
      <p class="initial-label">初次评价</p>
      <p class="initial-text">{{ initial.commentContent }}</p>
      <div v-if="initialImages.length" class="img-list">
        <img v-for="(img, idx) in initialImages" :key="idx" :src="toImageSrc(img)" alt="" />
      </div>
    </section>

    <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
      <el-form-item label="追评内容" prop="reCommentContent">
        <el-input
          v-model="form.reCommentContent"
          type="textarea"
          :rows="4"
          maxlength="300"
          show-word-limit
          placeholder="补充你的使用感受"
        />
      </el-form-item>
      <el-form-item label="追评图片（选填，最多5张）">
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
      <div class="footer-actions">
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">提交追评</el-button>
      </div>
    </template>
  </el-dialog>
  <ImageEditorDialog ref="imageEditorRef" />
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import type { FormInstance, FormRules, UploadRequestOptions } from 'element-plus';
import { commentApi, fileApi } from '@/api/modules';
import ImageEditorDialog from '@/components/business/ImageEditorDialog.vue';
import { normalizeCommentImagePath, serializeCommentImagePaths } from '@/utils/commentImagePaths';
import { resolveCommentUploadBlob } from '@/utils/imageUpload';
import { resolveImageUrl, splitImagePaths } from '@/utils/image';
import { formatUploadErrorMessage } from '@/utils/apiError';
import { toast } from '@/utils/toast';

const emit = defineEmits<{ success: [] }>();

const visible = ref(false);
const submitting = ref(false);
const formRef = ref<FormInstance>();
const imageEditorRef = ref<InstanceType<typeof ImageEditorDialog> | null>(null);
const initial = ref<Record<string, any>>({});
const imageList = ref<string[]>([]);

const form = reactive({
  orderId: '',
  reCommentContent: ''
});

const rules: FormRules = {
  reCommentContent: [{ required: true, message: '请输入追评内容', trigger: 'blur' }]
};

const initialImages = computed(() => splitImagePaths(initial.value.commentImages));

const toImageSrc = (path: unknown) => {
  const normalized = normalizeCommentImagePath(path);
  return normalized ? resolveImageUrl(normalized, { useThumbnail: false }) : '';
};

const reset = () => {
  form.orderId = '';
  form.reCommentContent = '';
  initial.value = {};
  imageList.value = [];
  formRef.value?.clearValidate();
};

const show = async (orderId: string) => {
  reset();
  form.orderId = orderId;
  visible.value = true;
  try {
    initial.value = (await commentApi.getComment(orderId)) || {};
  } catch {
    initial.value = {};
  }
};

const onUpload = async (options: UploadRequestOptions) => {
  try {
    const file = options.file as File;
    const blob = await resolveCommentUploadBlob(file, (f) => imageEditorRef.value!.open(f));
    const path = normalizeCommentImagePath(
      await fileApi.uploadImage(blob, true, 'comment', undefined, { skipPrepare: true })
    );
    if (path) {
      imageList.value.push(path);
      options.onSuccess?.(path);
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
    await commentApi.postReComment({
      orderId: form.orderId,
      reCommentContent: form.reCommentContent.trim(),
      reCommentImages: serializeCommentImagePaths(imageList.value) || undefined
    });
    toast.success('追评成功');
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

.initial-comment {
  margin-bottom: 16px;
  padding: 12px;
  border-radius: $radius-btn;
  background: $color-bg-subtle;
  border: 1px solid $color-border-light;

  .initial-label {
    margin: 0 0 8px;
    font-size: 12px;
    font-weight: 600;
    color: $color-text-muted;
  }

  .initial-text {
    margin: 0;
    font-size: 14px;
    line-height: 1.5;
    color: $color-text-body;
  }

  .img-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 8px;
  }

  .img-list img {
    width: 56px;
    height: 56px;
    object-fit: cover;
    border-radius: $radius-xs;
  }
}

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

.footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  width: 100%;
}
</style>
