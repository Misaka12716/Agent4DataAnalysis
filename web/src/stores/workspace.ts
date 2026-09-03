import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useWorkspaceStore = defineStore('workspace', () => {
  const currentProjectId = ref<number | null>(null)
  const currentSessionId = ref<string | null>(null)

  function setProject(projectId: number) {
    currentProjectId.value = projectId
  }

  function setSession(sessionId: string | null) {
    currentSessionId.value = sessionId
  }

  function clear() {
    currentProjectId.value = null
    currentSessionId.value = null
  }

  return {
    currentProjectId,
    currentSessionId,
    setProject,
    setSession,
    clear,
  }
})
