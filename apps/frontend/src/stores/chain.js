import { ref } from 'vue'
import { defineStore } from 'pinia'
import * as chainApi from 'src/api/chains'

export const useChainStore = defineStore('chain', () => {
  const chains = ref([])

  async function loadChains() {
    chains.value = await chainApi.listChains()
  }

  async function saveChain(id, payload) {
    const data = await chainApi.saveChain(id, payload)
    await loadChains()
    return data
  }

  async function deleteChain(id) {
    await chainApi.deleteChain(id)
    await loadChains()
  }

  async function startChain(id) {
    await chainApi.startChain(id)
    await loadChains()
  }

  async function cancelRun(chainId, runId) {
    await chainApi.cancelRun(chainId, runId)
    await loadChains()
  }

  return { chains, loadChains, saveChain, deleteChain, startChain, cancelRun, getRuns: chainApi.getRuns }
})
