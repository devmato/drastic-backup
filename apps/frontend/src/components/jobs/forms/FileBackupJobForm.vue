<template>
  <div class="q-mt-md">
    <PathSelectionPanel
      mode="include-exclude"
      :available-message="agentId && agentOnline ? '' : 'Agent must be online to browse directories.'"
      :load-entries="loadAgentEntries"
      :selection="configModel"
      :entry-options="entryOptions"
      :reload-key="agentId"
      browser-title="Directory Browser"
      selected-title="Selection"
      empty-label="No directories found or agent offline"
      error-message="Could not load directory listing"
      @update:selection="updateSelection"
    >
      <template #header>
        <div class="text-subtitle1">File Selection</div>
      </template>
      <template #advanced>
        <div class="text-subtitle2 q-mb-sm">Custom Exclude Pattern</div>
        <div class="row q-col-gutter-sm items-start">
          <div class="col">
            <q-input
              outlined
              dense
              v-model="excludePattern"
              label="Exclude pattern"
              placeholder="e.g. *.tmp or /var/cache/**"
              @keyup.enter="addCustomExcludePattern"
            />
          </div>
          <div class="col-auto">
            <q-btn color="primary" icon="remove" label="Add exclude" @click="addCustomExcludePattern" />
          </div>
        </div>
      </template>
    </PathSelectionPanel>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import PathSelectionPanel from 'components/PathSelectionPanel.vue'
import { useJobStore } from 'stores/job'
import { useAgentStore } from 'stores/agent'

const props = defineProps({
  modelValue: { type: Object, required: true },
  agentId: { type: [Number, String], default: null },
  agentOnline: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])

const jobStore = useJobStore()
const agentStore = useAgentStore()
const excludePattern = ref('')

const configModel = computed(() => createConfig(props.modelValue))

function entryOptions(entry) {
  if (entry.state === 'exclude' && !entry.explicit
    && (agentStore.agents.find(agent => String(agent.id) === String(props.agentId))?.protocol_version || 0) < 16) {
    return { includeDisabled: true, includeTooltip: 'Update the agent to include paths below excluded parents (protocol 16 required)' }
  }
  return {}
}

function createConfig(config = {}) {
  return {
    paths: (config.paths || []).map(path => ({ ...path })),
    exclude_patterns: (config.exclude_patterns || []).map(pattern => ({ ...pattern })),
  }
}

function emitConfig(nextConfig) {
  emit('update:modelValue', createConfig(nextConfig))
}

function updateSelection(nextSelection) {
  emitConfig(nextSelection)
}

async function loadAgentEntries(path) {
  return jobStore.getDirlist(props.agentId, path)
}

function addCustomExcludePattern() {
  const trimmedPattern = excludePattern.value.trim()

  if (!trimmedPattern) {
    return
  }

  emitConfig({
    paths: configModel.value.paths.filter(entry => entry.path !== trimmedPattern),
    exclude_patterns: [
      ...configModel.value.exclude_patterns.filter(entry => entry.path !== trimmedPattern),
      { path: trimmedPattern, group: 'pattern' },
    ],
  })
  excludePattern.value = ''
}

defineOptions({ name: 'FileBackupJobForm' })
</script>
