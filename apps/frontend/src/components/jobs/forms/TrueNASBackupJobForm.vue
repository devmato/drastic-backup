<template>
  <PathSelectionPanel class="q-mt-md" browser-title="Pools, Datasets and Files" selected-title="Selection"
    :selection="selection" :load-entries="loadEntries" :entry-options="entryOptions" :reload-key="reloadKey"
    :available-message="availableMessage" empty-selected-label="No paths selected yet."
    @update:selection="value => update(selectionConfig(value))">
    <template #header>
      <div class="text-subtitle1">TrueNAS Selection</div>
      <q-space />
      <q-btn flat dense no-caps no-wrap icon="settings" label="Connection" :to="`/agents/${agentId}?tab=connections`" target="_blank">
        <q-tooltip>Opens in a new tab; your job draft stays here.</q-tooltip>
      </q-btn>
      <q-btn flat dense no-caps no-wrap icon="refresh" label="Refresh" :disable="!!availableMessage" @click="refresh" />
    </template>
    <template #advanced>
      <q-input outlined dense type="textarea" autogrow label="Exclude patterns (one per line, relative to each dataset)" :model-value="(modelValue.exclude_patterns || []).join('\n')" @update:model-value="value => update({ exclude_patterns: value.split('\n').filter(Boolean) })" />
    </template>
  </PathSelectionPanel>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import PathSelectionPanel from 'components/PathSelectionPanel.vue'
import { useAgentStore } from 'stores/agent'
import { useJobStore } from 'stores/job'
import { browserKey, browserPath, browserSelection, isPathWithin, selectionConfig } from 'src/utils/truenas-selection'
import { pathSelectionOptions } from 'src/utils/path-selection'

const props = defineProps({ modelValue: { type: Object, required: true }, agentId: { type: [Number, String], default: null }, agentOnline: Boolean })
const emit = defineEmits(['update:modelValue'])
const agentStore = useAgentStore()
const jobStore = useJobStore()
const datasets = ref(null)
const reloadKey = ref(0)
const selection = computed(() => browserSelection(props.modelValue))
const availableMessage = computed(() => {
  if (!props.agentOnline) return 'Bring the agent online to browse datasets and files.'
  if ((agentStore.agents.find(agent => String(agent.id) === String(props.agentId))?.protocol_version || 0) < 14) {
    return 'Update the agent to use TrueNAS path selection (protocol 14 required).'
  }
  return ''
})

function update(patch) { emit('update:modelValue', { ...props.modelValue, ...patch }) }
function refresh() { datasets.value = null; reloadKey.value++ }
watch(() => [props.agentId, props.agentOnline], refresh)

function entryOptions(entry) {
  const original = value => ({ dataset: value.dataset, path: value.relativePath, group: value.group })
  const options = pathSelectionOptions({ ...entry, ...entry.selectionEntry }, selection.value,
    (child, parent) => isPathWithin(original(child), original(parent), datasets.value || []))
  const needsUpdate = options.state === 'exclude' && !options.explicit
    && (agentStore.agents.find(agent => String(agent.id) === String(props.agentId))?.protocol_version || 0) < 16
  return {
    ...options,
    includeDisabled: options.includeDisabled || needsUpdate,
    includeTooltip: needsUpdate ? 'Update the agent to include paths below excluded parents (protocol 16 required)' : '',
    // Locked/unmounted datasets must still be excludable from a selected parent.
    excludeDisabled: options.explicit && options.state === 'exclude',
  }
}

function browserEntry(dataset, relativePath = '.', group = 'dataset', details = {}) {
  return {
    ...details,
    name: relativePath === '.' ? dataset.id.split('/').pop() : relativePath.split('/').pop(),
    path: browserKey({ dataset: dataset.id, path: relativePath }),
    group, icon: group === 'dataset' ? 'storage' : group === 'file' ? 'description' : 'folder',
    file: group === 'file', readable: dataset.available && details.readable !== false,
    navigable: group === 'dataset' || (group === 'folder' && details.readable !== false),
    caption: group === 'dataset' ? dataset.error || 'Includes child datasets' : '',
    selectionEntry: { dataset: dataset.id, relativePath },
  }
}

async function loadEntries(path) {
  const agentId = props.agentId
  const version = reloadKey.value
  const known = datasets.value || await agentStore.getTrueNASDatasets(agentId)
  if (version === reloadKey.value) datasets.value = known
  const [datasetId, relativePath] = path === '/' ? ['', '.'] : JSON.parse(path)
  const dataset = known.find(item => item.id === datasetId)
  const children = relativePath === '.' ? known.filter(item => item.id.split('/').slice(0, -1).join('/') === datasetId) : []
  let entries = []
  if (dataset?.available) {
    const relative = relativePath === '.' ? '' : relativePath
    const local = `${dataset.path}${relative ? `/${relative}` : ''}`
    const listing = await jobStore.getDirlist(agentId, local)
    entries = listing.directories.filter(entry => entry.name !== '.zfs').map(entry => {
      const child = known.find(item => item.path === `${local}/${entry.name}`)
      return child ? browserEntry(child) : browserEntry(dataset, `${relative ? `${relative}/` : ''}${entry.name}`, entry.file ? 'file' : 'folder', entry)
    })
  }
  // Dataset discovery also exposes children whose parent isn't mounted in the agent.
  for (const child of children) {
    entries = entries.filter(entry => entry.path !== browserKey({ dataset: child.id, path: '.' }))
    entries.push(browserEntry(child))
  }
  const parentDataset = datasetId.split('/').slice(0, -1).join('/')
  const parent = relativePath === '.'
    ? (parentDataset ? browserKey({ dataset: parentDataset, path: '.' }) : '/')
    : browserKey({ dataset: datasetId, path: relativePath.split('/').slice(0, -1).join('/') || '.' })
  return { path, entries, parent_directory: parent, path_label: datasetId ? browserPath({ dataset: datasetId, path: relativePath }) : '/' }
}
</script>
