<template>
  <div>
    <q-tabs v-model="activeTab" dense align="left" class="text-primary">
      <q-tab name="schedules" label="Schedules" />
      <q-tab name="actions" label="Actions" />
    </q-tabs>

    <q-tab-panels v-model="activeTab">
      <q-tab-panel name="schedules">
        <JobSchedulesPanel
          :job-id="job.id"
          :model-value="job.schedules"
          :repositories="job.agent_repositories || []"
          :all-repositories="job.agent_repositories || []"
          :persist-immediately="true"
          @updated="emit('updated')"
        />
      </q-tab-panel>
      <q-tab-panel name="actions">
        <JobActionsPanel
          :job-id="job.id"
          :model-value="job.actions"
          :persist-immediately="true"
          :disabled="!job.agent_online"
          @updated="emit('updated')"
        />
      </q-tab-panel>
    </q-tab-panels>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import JobActionsPanel from 'components/jobs/panels/JobActionsPanel.vue'
import JobSchedulesPanel from 'components/jobs/panels/JobSchedulesPanel.vue'

defineProps({ job: { type: Object, required: true } })
const emit = defineEmits(['updated'])

const activeTab = ref('schedules')

defineOptions({ name: 'JobDetail' })
</script>
