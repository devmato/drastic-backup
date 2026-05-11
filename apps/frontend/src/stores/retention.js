import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from 'boot/axios'

export const useRetentionStore = defineStore('retention', () => {
  const retentions = ref([])
  const loading = ref(false)

  async function loadRetentions() {
    loading.value = true
    try {
      const response = await api.get('/retentions/')
      retentions.value = response.data
    } finally {
      loading.value = false
    }
  }

  async function createRetention(data) {
    await api.post('/retentions/', data)
    await loadRetentions()
  }

  async function updateRetention(retentionId, data) {
    await api.put(`/retentions/${retentionId}`, data)
    await loadRetentions()
  }

  async function deleteRetention(retentionId) {
    await api.delete(`/retentions/${retentionId}`)
    await loadRetentions()
  }

  return { retentions, loading, loadRetentions, createRetention, updateRetention, deleteRetention }
})
