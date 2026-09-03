<template>
  <el-card shadow="never" class="upload-card">
    <template #header>上传数据文件</template>
    <el-upload
      drag
      :auto-upload="false"
      :show-file-list="false"
      :disabled="!sessionId || uploading"
      :on-change="handleChange"
    >
      <div class="upload-inner">
        <p>拖拽或点击选择 CSV / Excel / 文本等文件</p>
        <p class="hint">将上传到当前会话工作区</p>
      </div>
    </el-upload>
  </el-card>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { UploadFile } from 'element-plus'
import { ElMessage } from 'element-plus'
import { uploadSessionFile } from '@/api/session'
import { extractErrorMessage } from '@/api/client'

const props = defineProps<{
  sessionId: string | null
}>()

const emit = defineEmits<{
  uploaded: []
}>()

const uploading = ref(false)

async function handleChange(uploadFile: UploadFile) {
  if (!props.sessionId || !uploadFile.raw) return
  uploading.value = true
  try {
    await uploadSessionFile(props.sessionId, uploadFile.raw)
    ElMessage.success(`已上传: ${uploadFile.name}`)
    emit('uploaded')
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.upload-card {
  margin-bottom: 16px;
}

.upload-inner {
  padding: 12px;
  text-align: center;
}

.hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: #9ca3af;
}
</style>
