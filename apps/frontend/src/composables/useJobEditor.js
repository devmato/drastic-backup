import { ref } from 'vue'
import * as jobsApi from 'src/api/jobs'
import { sameSchedule } from '../utils/schedule.js'

// Own the complete edit workflow; API calls do not reload shared state between each child write.
export function useJobEditor(jobStore, agentStore, userStore) {
  const submitting = ref(false)

  function schedulePayload(schedule, recoveryKey) {
    return {
      timing: schedule.timing,
      repository_id: schedule.repository_id,
      enabled: schedule.enabled,
      config: schedule.config || {},
      recovery_key: recoveryKey,
    }
  }

  async function saveSchedule(jobId, schedule, create) {
    await userStore.withRecoveryKey(key => create
      ? jobsApi.createSchedule(jobId, schedulePayload(schedule, key))
      : jobsApi.updateSchedule(schedule.id, schedulePayload(schedule, key)))
  }

  async function saveJob(data, editingJob, agentId) {
    // Leave the form draft intact: a later child-write failure cannot roll back earlier API commits.
    submitting.value = true
    let dirty = false
    let failed = false
    try {
      const { actions = [], schedules = [], ...payload } = data
      const originals = new Map((editingJob?.schedules || []).map(schedule => [schedule.id, schedule]))
      const changedSchedule = schedules.some(schedule => !sameSchedule(schedule, originals.get(schedule.id)))
      const agent = agentStore.agents.find(item => String(item.id) === String(agentId))
      if (schedules.some(schedule => schedule.config?.retry?.enabled) && (agent?.protocol_version || 0) < 18) {
        throw new Error('Update the agent to use backup retries (protocol 18 required)')
      }
      // Check before the first write, so an old agent can still accept a name-only edit.
      if (changedSchedule && (agent?.protocol_version || 0) < 24) {
        throw new Error('Update the agent to use account time zones (protocol 24 required)')
      }

      let id = editingJob?.id
      if (editingJob) {
        await jobsApi.updateJob(id, payload)
      } else {
        id = (await jobsApi.createJob({ ...payload, agent_id: agentId })).id
      }
      dirty = true
      const saved = await jobsApi.getJob(id)

      for (const action of actions) {
        if (!editingJob || String(action.id).startsWith('draft-')) {
          await jobsApi.createAction(id, action)
        } else {
          await jobsApi.updateAction(action.id, action)
        }
      }
      if (editingJob) {
        const actionIds = new Set(actions.map(action => action.id))
        for (const action of saved.actions) {
          if (!actionIds.has(action.id)) await jobsApi.deleteAction(action.id)
        }
      }

      for (const schedule of schedules) {
        const create = !editingJob || String(schedule.id).startsWith('draft-')
        if (create || !sameSchedule(schedule, saved.schedules.find(item => item.id === schedule.id))) {
          await saveSchedule(id, schedule, create)
        }
      }
      if (editingJob) {
        const scheduleIds = new Set(schedules.map(schedule => schedule.id))
        for (const schedule of saved.schedules) {
          if (!scheduleIds.has(schedule.id)) await jobsApi.deleteSchedule(schedule.id)
        }
      }

      return id
    } catch (error) {
      failed = true
      throw error
    } finally {
      try {
        if (dirty) {
          // Child writes are separate API transactions. Refresh partial saves too, keeping the original error.
          const refresh = Promise.all([jobStore.loadJobs(), agentStore.loadAgents()])
          if (failed) await refresh.catch(() => {})
          else await refresh
        }
      } finally {
        submitting.value = false
      }
    }
  }

  return { submitting, saveJob }
}
