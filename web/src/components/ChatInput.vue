<template>
  <div class="chat-input">
    <el-input
      v-model="text"
      type="textarea"
      :rows="3"
      placeholder="描述你的分析需求，例如：对上传的 CSV 做描述性统计并生成图表"
      :disabled="disabled"
      @keydown.ctrl.enter="submit"
    />
    <div class="actions">
      <el-button
        v-if="running"
        type="danger"
        plain
        @click="$emit('stop')"
      >
        停止
      </el-button>
      <el-button
        type="primary"
        :loading="running"
        :disabled="disabled || !text.trim()"
        @click="submit"
      >
        开始分析
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

defineProps<{
  disabled?: boolean
  running?: boolean
}>()

const emit = defineEmits<{
  submit: [text: string]
  stop: []
}>()

const text = ref('')

function submit() {
  const value = text.value.trim()
  if (!value) return
  emit('submit', value)
}
</script>

<style scoped>
.chat-input {
  margin-top: 16px;
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
</style>
