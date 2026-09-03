<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <el-button text @click="goBack">← 项目列表</el-button>
      <h2>{{ projectName || `项目 #${projectId}` }}</h2>
      <el-button type="primary" size="small" :loading="creating" @click="handleCreateSession">
        新建会话
      </el-button>
    </div>

    <el-scrollbar class="session-list">
      <div
        v-for="session in sessions"
        :key="session.session_id"
        class="session-item"
        :class="{ active: session.session_id === modelValue }"
        @click="selectSession(session.session_id)"
      >
        <div class="session-title">{{ session.title || '未命名会话' }}</div>
        <div class="session-id">{{ session.session_id.slice(0, 8) }}…</div>
      </div>
      <el-empty v-if="!loading && sessions.length === 0" description="暂无会话" :image-size="64" />
    </el-scrollbar>

    <div v-if="modelValue" class="file-tree">
      <h3>工作区文件</h3>
      <el-tree
        v-if="tree.length"
        :data="tree"
        :props="{ label: 'name', children: 'children' }"
        default-expand-all
      />
      <p v-else class="empty-files">暂无文件，请先上传</p>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getProject } from '@/api/project'
import { createSession, getWorkspaceTree, listSessions } from '@/api/session'
import { extractErrorMessage } from '@/api/client'
import type { SessionItem, WorkspaceTreeNode } from '@/types/api'

const props = defineProps<{
  projectId: number
  modelValue: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [sessionId: string | null]
  refresh: []
}>()

const router = useRouter()
const sessions = ref<SessionItem[]>([])
const tree = ref<WorkspaceTreeNode[]>([])
const projectName = ref('')
const loading = ref(false)
const creating = ref(false)

async function loadSessions() {
  loading.value = true
  try {
    sessions.value = await listSessions(props.projectId)
    if (!props.modelValue && sessions.value.length > 0) {
      emit('update:modelValue', sessions.value[0].session_id)
    }
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    loading.value = false
  }
}

async function loadTree() {
  if (!props.modelValue) {
    tree.value = []
    return
  }
  try {
    tree.value = await getWorkspaceTree(props.modelValue)
  } catch (error) {
    tree.value = []
  }
}

async function loadProject() {
  try {
    const project = await getProject(props.projectId)
    projectName.value = project.name
  } catch {
    projectName.value = ''
  }
}

function selectSession(sessionId: string) {
  emit('update:modelValue', sessionId)
}

async function handleCreateSession() {
  creating.value = true
  try {
    const session = await createSession(props.projectId)
    await loadSessions()
    emit('update:modelValue', session.session_id)
    ElMessage.success('会话已创建')
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    creating.value = false
  }
}

function goBack() {
  router.push({ name: 'projects' })
}

async function refresh() {
  await loadSessions()
  await loadTree()
}

watch(
  () => props.modelValue,
  () => {
    void loadTree()
  },
)

watch(
  () => props.projectId,
  () => {
    void loadProject()
    void loadSessions()
  },
)

onMounted(() => {
  void loadProject()
  void loadSessions()
})

defineExpose({ refresh })
</script>

<style scoped>
.sidebar {
  width: 280px;
  min-width: 280px;
  border-right: 1px solid #e5e7eb;
  background: #fff;
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.sidebar-header {
  padding: 16px;
  border-bottom: 1px solid #e5e7eb;
}

.sidebar-header h2 {
  margin: 8px 0 12px;
  font-size: 1rem;
}

.session-list {
  flex: 1;
  padding: 8px;
}

.session-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 4px;
}

.session-item:hover {
  background: #f3f4f6;
}

.session-item.active {
  background: #eff6ff;
  border: 1px solid #bfdbfe;
}

.session-title {
  font-size: 14px;
  font-weight: 500;
}

.session-id {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 2px;
}

.file-tree {
  padding: 12px 16px;
  border-top: 1px solid #e5e7eb;
  max-height: 240px;
  overflow: auto;
}

.file-tree h3 {
  margin: 0 0 8px;
  font-size: 13px;
  color: #6b7280;
}

.empty-files {
  font-size: 12px;
  color: #9ca3af;
  margin: 0;
}
</style>
