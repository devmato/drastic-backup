// Job HTTP contracts. State refresh and user interaction belong to the caller.
import { api } from 'boot/axios'

export async function listJobs() {
  return (await api.get('/jobs/')).data
}

export async function previewSchedule(data) {
  return (await api.post('/jobs/schedules/preview', data)).data
}

export async function getJob(id) {
  return (await api.get(`/jobs/${id}`)).data
}

export async function createJob(data) {
  return (await api.post('/jobs/', data)).data
}

export async function updateJob(id, data) {
  return (await api.put(`/jobs/${id}`, data)).data
}

export async function deleteJob(id) {
  await api.delete(`/jobs/${id}`)
}

export async function cancelJob(id) {
  await api.post(`/jobs/${id}/cancel`)
}

export async function getDirlist(agentId, baseDirectory = '/') {
  return (await api.get(`/jobs/dirlist?agent_id=${agentId}&base_directory=${encodeURIComponent(baseDirectory)}`)).data
}

export async function getProxmoxGuests(agentId) {
  return (await api.get(`/jobs/proxmox-guests?agent_id=${agentId}`)).data.guests
}

export async function getContainers(agentId) {
  return (await api.get(`/jobs/containers?agent_id=${agentId}`)).data.containers
}

export async function createSchedule(jobId, data) {
  await api.post(`/jobs/${jobId}/schedules`, data)
}

export async function updateSchedule(id, data) {
  await api.put(`/jobs/schedules/${id}`, data)
}

export async function deleteSchedule(id) {
  await api.delete(`/jobs/schedules/${id}`)
}

export async function createAction(jobId, data) {
  await api.post(`/jobs/${jobId}/actions`, data)
}

export async function updateAction(id, data) {
  await api.put(`/jobs/actions/${id}`, data)
}

export async function deleteAction(id) {
  await api.delete(`/jobs/actions/${id}`)
}
