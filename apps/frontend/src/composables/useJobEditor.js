import { ref } from 'vue'
import * as jobsApi from 'src/api/jobs'
import { sameSchedule } from '../utils/schedule.js'

// Own the complete edit workflow; API calls do not reload shared state between each child write.
export function useJobEditor(jobStore, agentStore, userStore) {
  const submitting = ref(false)
  const createdJobId = ref(null)
  const actionIds = new Map()
  const scheduleIds = new Map()

  function reset() {
    createdJobId.value = null
    actionIds.clear()
    scheduleIds.clear()
  }

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
    return userStore.withRecoveryKey(key => create
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

      let id = editingJob?.id || createdJobId.value
      if (id) {
        await jobsApi.updateJob(id, payload)
      } else {
        id = (await jobsApi.createJob({ ...payload, agent_id: agentId })).id
        createdJobId.value = id
      }
      dirty = true
      const saved = await jobsApi.getJob(id)

      for (const action of actions) {
        const actionId = actionIds.get(action.id) || (String(action.id).startsWith('draft-') ? null : action.id)
        if (!actionId) {
          actionIds.set(action.id, (await jobsApi.createAction(id, action)).id)
        } else {
          await jobsApi.updateAction(actionId, action)
        }
      }
      const keptActionIds = new Set(actions.map(action => actionIds.get(action.id) || action.id))
      for (const action of saved.actions) {
        if (!keptActionIds.has(action.id)) await jobsApi.deleteAction(action.id)
      }

      for (const schedule of schedules) {
        const scheduleId = scheduleIds.get(schedule.id) || (String(schedule.id).startsWith('draft-') ? null : schedule.id)
        if (!scheduleId) {
          scheduleIds.set(schedule.id, (await saveSchedule(id, schedule, true)).id)
        } else if (!sameSchedule(schedule, saved.schedules.find(item => item.id === scheduleId))) {
          await saveSchedule(id, { ...schedule, id: scheduleId }, false)
        }
      }
      const keptScheduleIds = new Set(schedules.map(schedule => scheduleIds.get(schedule.id) || schedule.id))
      for (const schedule of saved.schedules) {
        if (!keptScheduleIds.has(schedule.id)) await jobsApi.deleteSchedule(schedule.id)
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

  return { submitting, createdJobId, saveJob, reset }
}
