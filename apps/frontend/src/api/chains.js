import { api } from 'boot/axios'

export async function listChains() {
  return (await api.get('/chains/')).data
}

export async function saveChain(id, payload) {
  return (id ? await api.put(`/chains/${id}`, payload) : await api.post('/chains/', payload)).data
}

export async function deleteChain(id) {
  await api.delete(`/chains/${id}`)
}

export async function startChain(id) {
  await api.post(`/chains/${id}/run`)
}

export async function cancelRun(chainId, runId) {
  await api.post(`/chains/${chainId}/runs/${runId}/cancel`)
}

export async function getRuns(chainId, beforeId) {
  return (await api.get(`/chains/${chainId}/runs`, { params: { before_id: beforeId } })).data
}
