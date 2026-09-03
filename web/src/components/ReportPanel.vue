<template>
  <el-card shadow="never" class="report-card">
    <template #header>
      <span>分析报告</span>
      <el-tag v-if="status" :type="statusType" size="small">{{ status }}</el-tag>
    </template>
    <div v-if="html" class="report-body markdown-body" v-html="html" />
    <p v-else class="empty">报告将在此流式生成</p>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'

const props = defineProps<{
  content: string
  status?: 'idle' | 'running' | 'done' | 'error'
}>()

const html = computed(() => {
  if (!props.content) return ''
  return marked.parse(props.content, { async: false }) as string
})

const statusType = computed(() => {
  switch (props.status) {
    case 'running':
      return 'warning'
    case 'done':
      return 'success'
    case 'error':
      return 'danger'
    default:
      return 'info'
  }
})
</script>

<style scoped>
.report-card :deep(.el-card__header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.report-body {
  min-height: 120px;
  line-height: 1.6;
  font-size: 14px;
}

.report-body :deep(pre) {
  background: #f3f4f6;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
}

.report-body :deep(code) {
  font-family: ui-monospace, monospace;
}

.empty {
  margin: 0;
  color: #9ca3af;
  font-size: 13px;
}
</style>
