<template>
  <div class="q-mt-md">
    <PathSelectionPanel
      mode="include-exclude"
      :available-message="agentId && agentOnline ? '' : 'Agent must be online to browse directories.'"
      :load-entries="loadAgentEntries"
      :selection="configModel"
      :reload-key="agentId"
      browser-title="Directory Browser"
      selected-title="Selected Paths"
      empty-label="No directories found or agent offline"
      error-message="Could not load directory listing"
      @update:selection="updateSelection"
    >
      <template #header>
        <div class="text-subtitle1">File Selection</div>
        <q-space />
        <q-badge color="grey-7" :label="`${selectedEntries.length} selected`" />
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

const props = defineProps({
  modelValue: { type: Object, required: true },
  agentId: { type: [Number, String], default: null },
  agentOnline: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])

const jobStore = useJobStore()
const excludePattern = ref('')

const configModel = computed(() => createConfig(props.modelValue))

const selectedEntries = computed(() => [
  ...configModel.value.paths.map(entry => ({ ...entry, exclude: false })),
  ...configModel.value.exclude_patterns.map(entry => ({ ...entry, exclude: true })),
])

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
