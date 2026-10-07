<template>
  <SelectionLayout class="q-mt-md" available-title="Available Datasets" selected-title="Selection"
    :available-entries="availableRows" :selected-entries="selectedRows"
    :available-message="agentOnline ? '' : 'Bring the agent online to discover datasets.'"
    :available-error="error" :loading="loading" loading-label="Loading datasets…"
    empty-available-label="No supported datasets found." empty-selected-label="No datasets selected yet."
    @select="addDataset" @exclude="excludeDataset" @remove="removeRule">
    <template #header>
      <div class="text-subtitle1">Dataset Selection</div>
      <q-space />
      <q-badge color="grey-7" :label="scopeLabel" />
      <div class="row q-gutter-xs">
        <q-btn flat dense no-caps no-wrap icon="settings" label="Connection" :to="`/agents/${agentId}?tab=connections`" target="_blank">
          <q-tooltip>Opens in a new tab; your job draft stays here.</q-tooltip>
        </q-btn>
        <q-btn flat dense no-caps no-wrap icon="refresh" label="Refresh" :loading="loading" :disable="!agentOnline" @click="loadDatasets" />
      </div>
    </template>
    <template #options>
      <q-toggle :model-value="modelValue.include_children ?? false" label="Include child datasets" @update:model-value="value => update({ include_children: value })" />
      <div class="text-caption">{{ modelValue.include_children ? 'Child datasets, including newly created ones, are included automatically.' : 'Only explicitly selected datasets are included.' }} Exclusions omit a dataset and all its children.</div>
      <div v-if="!supportsExclusions" class="text-caption q-mt-xs">Update the agent to exclude datasets (protocol 13 required).</div>
    </template>
    <template #advanced>
      <q-input outlined dense type="textarea" autogrow label="Exclude patterns (one per line, relative to each dataset)" :model-value="(modelValue.exclude_patterns || []).join('\n')" @update:model-value="value => update({ exclude_patterns: value.split('\n').filter(Boolean) })" />
    </template>
  </SelectionLayout>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import SelectionLayout from 'components/SelectionLayout.vue'
import { useAgentStore } from 'stores/agent'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'
import { hasTrueNASSelection, isDatasetWithin } from 'src/utils/truenas-selection'

const props = defineProps({ modelValue: { type: Object, required: true }, agentId: { type: [Number, String], default: null }, agentOnline: Boolean })
const emit = defineEmits(['update:modelValue'])
const agentStore = useAgentStore()
const datasets = ref([])
const loading = ref(false)
const error = ref('')
let loadVersion = 0
const selectedIds = computed(() => props.modelValue.datasets || [])
const excludedIds = computed(() => props.modelValue.exclude_datasets || [])
const supportsExclusions = computed(() => (agentStore.agents.find(agent => String(agent.id) === String(props.agentId))?.protocol_version || 0) >= 13)
const selectedDatasets = computed(() => selectedIds.value.map(id => datasets.value.find(dataset => dataset.id === id) || { id }))
function includedVia(id) {
  return selectedIds.value.find(parent => parent === id)
    || (props.modelValue.include_children ? selectedIds.value.find(parent => isDatasetWithin(id, parent)) : undefined)
}
function excludedVia(id) { return excludedIds.value.find(parent => isDatasetWithin(id, parent)) }
function canExclude(id) {
  return supportsExclusions.value && !!includedVia(id) && !excludedVia(id)
    && hasTrueNASSelection({ ...props.modelValue, exclude_datasets: [...excludedIds.value, id] })
}
const scopeLabel = computed(() => {
  const count = datasets.value.filter(dataset => includedVia(dataset.id) && !excludedVia(dataset.id)).length
  const known = props.agentOnline && !loading.value && !error.value
  return `${known ? `${count} included` : `${selectedIds.value.length} selected · scope unavailable`} · ${excludedIds.value.length} ${excludedIds.value.length === 1 ? 'exclusion' : 'exclusions'}`
})
function datasetRow(dataset) {
  return { ...dataset, label: dataset.id, caption: dataset.error || dataset.path || 'Details unavailable' }
}
const availableRows = computed(() => datasets.value.map(dataset => {
  const included = includedVia(dataset.id)
  const excluded = excludedVia(dataset.id)
  return {
    ...datasetRow(dataset),
    state: excluded ? 'exclude' : included ? 'include' : undefined,
    note: excluded ? `Excluded via ${excluded}` : included ? `Included via ${included}` : '',
    actions: [
      { name: 'select', icon: 'add', color: 'positive', label: 'Select dataset',
        disable: !dataset.available || (!!included && !excluded) || (!!excluded && excluded !== dataset.id) },
      { name: 'exclude', icon: 'remove', color: 'negative', label: 'Exclude dataset and children',
        disable: !canExclude(dataset.id), tooltip: supportsExclusions.value ? 'Exclude dataset and children' : 'Update the agent (protocol 13 required)' },
    ],
  }
}))
const selectedRows = computed(() => [
  ...selectedDatasets.value.map(dataset => ({
    ...datasetRow(dataset), id: `include:${dataset.id}`, datasetId: dataset.id, state: 'include',
    note: excludedVia(dataset.id) ? `Excluded via ${excludedVia(dataset.id)}` : props.modelValue.include_children ? 'Includes child datasets' : 'Include',
  })),
  ...excludedIds.value.map(id => ({
    ...datasetRow(datasets.value.find(dataset => dataset.id === id) || { id }),
    id: `exclude:${id}`, datasetId: id, state: 'exclude',
    note: includedVia(id) || selectedIds.value.some(name => isDatasetWithin(name, id))
      ? 'Excluded with child datasets' : 'Excluded with child datasets · outside current selection',
  })),
])
function update(patch) { emit('update:modelValue', { ...props.modelValue, ...patch }) }
function addDataset(dataset) {
  const excluded = excludedVia(dataset.id)
  if (!dataset.available || (excluded && excluded !== dataset.id)) return
  update({
    datasets: includedVia(dataset.id) ? [...selectedIds.value] : [...selectedIds.value, dataset.id],
    exclude_datasets: excludedIds.value.filter(id => id !== dataset.id),
  })
}
function excludeDataset(dataset) {
  if (canExclude(dataset.id)) update({ exclude_datasets: [...excludedIds.value, dataset.id] })
}
function removeRule(entry) {
  const field = entry.state === 'exclude' ? 'exclude_datasets' : 'datasets'
  update({ [field]: (props.modelValue[field] || []).filter(id => id !== entry.datasetId) })
}

async function loadDatasets() {
  const version = ++loadVersion
  error.value = ''
  datasets.value = []
  loading.value = false
  if (!props.agentId || !props.agentOnline) return
  loading.value = true
  try {
    const result = await agentStore.getTrueNASDatasets(props.agentId)
    if (version === loadVersion) datasets.value = result
  } catch (e) {
    if (version === loadVersion && !shouldIgnoreApiError(e)) error.value = getApiErrorMessage(e)
  } finally {
    if (version === loadVersion) loading.value = false
  }
}
watch(() => [props.agentId, props.agentOnline], loadDatasets, { immediate: true })
</script>
