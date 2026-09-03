<template>
  <div class="page">
    <header class="header">
      <h1>我的项目</h1>
    </header>

    <div class="toolbar">
      <el-input
        v-model="newProjectName"
        placeholder="新项目名称"
        style="max-width: 280px"
        @keyup.enter="handleCreate"
      />
      <el-button type="primary" :loading="creating" @click="handleCreate">创建项目</el-button>
    </div>

    <el-skeleton v-if="loading" :rows="4" animated />

    <el-empty v-else-if="projects.length === 0" description="暂无项目，请创建一个" />

    <div v-else class="grid">
      <el-card
        v-for="project in projects"
        :key="project.id"
        class="project-card"
        shadow="hover"
        @click="openProject(project.id)"
      >
        <h3>{{ project.name }}</h3>
        <p class="meta">ID: {{ project.id }}</p>
        <p v-if="project.status" class="meta">状态: {{ project.status }}</p>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { createProject, listProjects } from '@/api/project'
import { extractErrorMessage } from '@/api/client'
import type { Project } from '@/types/api'

const router = useRouter()

const projects = ref<Project[]>([])
const loading = ref(false)
const creating = ref(false)
const newProjectName = ref('')

async function loadProjects() {
  loading.value = true
  try {
    projects.value = await listProjects()
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    loading.value = false
  }
}

async function handleCreate() {
  const name = newProjectName.value.trim()
  if (!name) {
    ElMessage.warning('请输入项目名称')
    return
  }
  creating.value = true
  try {
    const project = await createProject(name)
    newProjectName.value = ''
    ElMessage.success('项目已创建')
    await loadProjects()
    openProject(project.id)
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    creating.value = false
  }
}

function openProject(projectId: number) {
  router.push({ name: 'analysis', params: { projectId } })
}

onMounted(() => {
  void loadProjects()
})
</script>

<style scoped>
.page {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px;
}

.header {
  margin-bottom: 24px;
}

.header h1 {
  margin: 0;
}

.toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 16px;
}

.project-card {
  cursor: pointer;
}

.project-card h3 {
  margin: 0 0 8px;
}

.meta {
  margin: 4px 0;
  font-size: 13px;
  color: #6b7280;
}
</style>
