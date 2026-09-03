<template>
  <div class="analysis-page">
    <SessionSidebar
      ref="sidebarRef"
      :project-id="projectId"
      v-model="sessionId"
      @refresh="onSidebarRefresh"
    />

    <main class="main">
      <div v-if="!sessionId" class="placeholder">
        <el-empty description="请选择或新建会话" />
      </div>
      <template v-else>
        <FileUploadPanel :session-id="sessionId" @uploaded="refreshSidebar" />
        <PipelineTimeline :events="events" />
        <ReportPanel :content="reportContent" :status="reportStatus" />
        <ChatInput
          :disabled="!sessionId"
          :running="analyzing"
          @submit="handleAnalyze"
          @stop="handleStop"
        />
      </template>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import SessionSidebar from '@/components/SessionSidebar.vue'
import FileUploadPanel from '@/components/FileUploadPanel.vue'
import ChatInput from '@/components/ChatInput.vue'
import PipelineTimeline from '@/components/PipelineTimeline.vue'
import ReportPanel from '@/components/ReportPanel.vue'
import { runAnalysisStream } from '@/api/analysis'
import { extractErrorMessage } from '@/api/client'
import { useWorkspaceStore } from '@/stores/workspace'
import type { SsePayload } from '@/types/api'

const props = defineProps<{
  projectId: number
}>()

const workspace = useWorkspaceStore()
workspace.setProject(props.projectId)

const sessionId = ref<string | null>(workspace.currentSessionId)
const sidebarRef = ref<InstanceType<typeof SessionSidebar> | null>(null)

const events = ref<SsePayload[]>([])
const reportContent = ref('')
const reportStatus = ref<'idle' | 'running' | 'done' | 'error'>('idle')
const analyzing = ref(false)
let abortController: AbortController | null = null

function refreshSidebar() {
  sidebarRef.value?.refresh()
}

function onSidebarRefresh() {
  workspace.setSession(sessionId.value)
}

async function handleAnalyze(input: string) {
  if (!sessionId.value) return

  events.value = []
  reportContent.value = ''
  reportStatus.value = 'running'
  analyzing.value = true
  abortController = new AbortController()

  try {
    await runAnalysisStream({
      sessionId: sessionId.value,
      inputData: input,
      signal: abortController.signal,
      onEvent: (event) => {
        if (event.type === 'report_chunk' && event.content) {
          reportContent.value += event.content
        }
        events.value.push(event)

        if (event.type === 'streaming_ended') {
          reportStatus.value = 'done'
        }
        if (event.type === 'error' || event.type === 'streaming_error') {
          reportStatus.value = 'error'
          ElMessage.error(event.message || event.error || '分析失败')
        }
      },
    })
    if (reportStatus.value === 'running') {
      reportStatus.value = 'done'
    }
  } catch (error) {
    if (abortController?.signal.aborted) {
      reportStatus.value = 'idle'
      ElMessage.info('已取消分析')
    } else {
      reportStatus.value = 'error'
      ElMessage.error(extractErrorMessage(error))
    }
  } finally {
    analyzing.value = false
    abortController = null
  }
}

function handleStop() {
  abortController?.abort()
}
</script>

<style scoped>
.analysis-page {
  display: flex;
  min-height: 100vh;
}

.main {
  flex: 1;
  padding: 20px 24px;
  overflow: auto;
}

.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
}
</style>
