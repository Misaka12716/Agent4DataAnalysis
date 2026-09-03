<template>
  <el-card shadow="never" class="timeline-card">
    <template #header>
      <span>编排过程</span>
      <el-tag v-if="events.length" size="small" type="info">{{ events.length }} 条</el-tag>
    </template>
    <el-scrollbar max-height="320px">
      <el-collapse v-if="events.length">
        <el-collapse-item
          v-for="(event, index) in events"
          :key="index"
          :title="formatTitle(event)"
        >
          <pre class="event-json">{{ formatBody(event) }}</pre>
        </el-collapse-item>
      </el-collapse>
      <p v-else class="empty">分析开始后将在此显示 Planner / Coder / Worker 等阶段事件</p>
    </el-scrollbar>
  </el-card>
</template>

<script setup lang="ts">
import type { SsePayload } from '@/types/api'

defineProps<{
  events: SsePayload[]
}>()

function formatTitle(event: SsePayload): string {
  const ts = event.timestamp ? ` · ${event.timestamp}` : ''
  if (event.type === 'orchestrator' && event.data && typeof event.data === 'object') {
    const d = event.data as { next?: string; reason?: string }
    return `orchestrator → ${d.next ?? '?'}${ts}`
  }
  if (event.type === 'report_chunk') {
    return `report_chunk${ts}`
  }
  if (event.type === 'error' || event.type === 'streaming_error') {
    return `${event.type}${ts}`
  }
  return `${event.type}${ts}`
}

function formatBody(event: SsePayload): string {
  if (event.type === 'report_chunk') {
    return event.content ?? ''
  }
  if (event.message) return event.message
  if (event.error) return event.error
  try {
    return JSON.stringify(event.data ?? event, null, 2)
  } catch {
    return String(event)
  }
}
</script>

<style scoped>
.timeline-card :deep(.el-card__header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.event-json {
  margin: 0;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  background: #f9fafb;
  padding: 8px;
  border-radius: 6px;
}

.empty {
  margin: 0;
  color: #9ca3af;
  font-size: 13px;
}
</style>
