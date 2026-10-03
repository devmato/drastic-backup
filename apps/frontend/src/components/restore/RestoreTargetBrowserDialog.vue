<template>
  <q-dialog v-model="dialogVisible" :maximized="$q.screen.lt.md">
    <q-card class="db-dialog-card-lg">
      <q-card-section>
        <div class="text-h6">Select Restore Location</div>
        <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Choose a local directory on the selected restore agent.</div>
      </q-card-section>

      <q-card-section>
        <PathBrowser
          v-if="agentId"
          mode="pick-directory"
          :load-entries="loadAgentEntries"
          :reload-key="agentId"
          height="360px"
          empty-label="No directories found or agent offline"
          error-message="Could not load target directories"
          @path-change="currentPath = $event"
        />
        <q-banner v-else :class="$q.dark.isActive ? 'bg-grey-9 text-grey-4' : 'bg-grey-2 text-grey-8'">Select a restore agent first.</q-banner>
      </q-card-section>

      <q-card-actions align="right" class="q-pa-md">
        <q-btn flat no-caps label="Cancel" v-close-popup />
        <q-btn unelevated no-caps color="primary" label="Use this path" :disable="!currentPath" @click="pickPath(currentPath)" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { ref } from 'vue'
import PathBrowser from 'components/PathBrowser.vue'
import { useJobStore } from 'stores/job'

const props = defineProps({
  agentId: { type: [Number, String], default: null },
})
const emit = defineEmits(['pick'])

const jobStore = useJobStore()
const currentPath = ref('/')

const dialogVisible = defineModel({ type: Boolean, required: true })

async function loadAgentEntries(path) {
  return jobStore.getDirlist(props.agentId, path)
}

function pickPath(path) {
  emit('pick', path)
  dialogVisible.value = false
}

defineOptions({ name: 'RestoreTargetBrowserDialog' })
</script>
