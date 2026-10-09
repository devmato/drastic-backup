import { api } from 'boot/axios'

export async function listAgents() {
  return (await api.get('/agents/')).data
}

export async function deleteAgent(id) {
  await api.delete(`/agents/${id}`)
}

export async function updateAgent(id, alias) {
  return (await api.put(`/agents/${id}`, { alias })).data
}

export async function updateAgentRepositories(id, repositoryIds, recoveryKey = null) {
  const data = { repository_ids: repositoryIds }
  if (recoveryKey) data.recovery_key = recoveryKey
  await api.put(`/agents/${id}/repositories`, data)
}

export async function syncAgent(id, recoveryKey = null) {
  const data = {}
  if (recoveryKey) data.recovery_key = recoveryKey
  await api.post(`/agents/${id}/sync`, data)
}

export async function runAction(id, action) {
  return (await api.post(`/agents/${id}/actions/${action}`)).data
}

export async function getAgentOperations(id, params = {}) {
  return (await api.get(`/agents/${id}/operations?${new URLSearchParams(params)}`)).data
}

export async function getOperation(id) {
  return (await api.get(`/agents/operations/${id}`)).data
}

export async function deleteOperation(id) {
  await api.delete(`/agents/operations/${id}`)
}

export async function getInstallOptions() {
  return (await api.get('/agents/install-options')).data
}

export async function getProxmoxSettings(id) {
  return (await api.get(`/agents/${id}/proxmox-settings`)).data
}

export async function updateProxmoxSettings(id, settings) {
  return (await api.put(`/agents/${id}/proxmox-settings`, settings)).data
}

export async function testProxmoxSettings(id, settings) {
  return (await api.post(`/agents/${id}/proxmox-settings/test`, settings)).data
}

export async function deleteConnection(id, kind) {
  await api.delete(`/agents/${id}/connections/${kind}`)
}

export async function getTrueNASSettings(id) {
  return (await api.get(`/agents/${id}/truenas-settings`)).data
}

export async function updateTrueNASSettings(id, settings) {
  return (await api.put(`/agents/${id}/truenas-settings`, settings)).data
}

export async function testTrueNASSettings(id, settings) {
  return (await api.post(`/agents/${id}/truenas-settings/test`, settings)).data
}

export async function cleanupTrueNAS(id) {
  return (await api.post(`/agents/${id}/truenas-settings/cleanup`)).data
}

export async function getTrueNASDatasets(id) {
  return (await api.get(`/agents/${id}/truenas-datasets`)).data.datasets
}
