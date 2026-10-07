import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from 'boot/axios'

export const useChainStore = defineStore('chain', () => {
  const chains = ref([])

  async function loadChains() {
    const { data } = await api.get('/chains/')
    chains.value = data
  }

  async function saveChain(id, payload) {
    const { data } = id ? await api.put(`/chains/${id}`, payload) : await api.post('/chains/', payload)
    await loadChains()
    return data
  }

  async function deleteChain(id) {
    await api.delete(`/chains/${id}`)
    await loadChains()
  }

  async function startChain(id) {
    await api.post(`/chains/${id}/run`)
    await loadChains()
  }

  async function cancelRun(chainId, runId) {
    await api.post(`/chains/${chainId}/runs/${runId}/cancel`)
    await loadChains()
  }

  async function getRuns(chainId, beforeId) {
    const { data } = await api.get(`/chains/${chainId}/runs`, { params: { before_id: beforeId } })
    return data
  }

  return { chains, loadChains, saveChain, deleteChain, startChain, cancelRun, getRuns }
})
