<template>
  <SelectionLayout class="q-mt-md" available-title="Available Datasets" selected-title="Selected Datasets">
    <template #header>
      <div class="text-subtitle1">Dataset Selection</div>
      <q-space />
      <q-badge color="grey-7" :label="`${selectedIds.length} selected`" />
      <div class="row q-gutter-xs">
        <q-btn flat dense no-caps no-wrap icon="settings" label="Connection" :to="`/agents/${agentId}?tab=connections`" target="_blank">
          <q-tooltip>Opens in a new tab; your job draft stays here.</q-tooltip>
        </q-btn>
        <q-btn flat dense no-caps no-wrap icon="refresh" label="Refresh" :loading="loading" :disable="!agentOnline" @click="loadDatasets" />
      </div>
    </template>
    <template #options>
      <q-toggle :model-value="modelValue.include_children ?? false" label="Include child datasets" @update:model-value="value => update({ include_children: value })" />
      <div class="text-caption">Include child datasets to back up all supported filesystem datasets below your selection, including ones added later. Every included dataset must be mounted and readable by the agent. Each dataset gets its own temporary ZFS snapshot and file backup. Snapshots are removed after the run.</div>
    </template>
    <template #available>
      <q-card flat bordered>
        <q-scroll-area style="height: 320px">
          <q-banner v-if="!agentOnline">Bring the agent online to discover datasets.</q-banner>
          <q-banner v-else-if="error" class="bg-negative text-white">{{ error }}</q-banner>
          <div v-else-if="loading" class="text-grey q-pa-md">Loading datasets…</div>
          <q-list v-else-if="availableDatasets.length" separator>
            <q-item v-for="dataset in availableDatasets" :key="dataset.id">
              <q-item-section>
                <q-item-label class="db-break-word">{{ dataset.id }}</q-item-label>
                <q-item-label caption class="db-break-word">{{ dataset.error || dataset.path }}</q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-btn flat dense round icon="add" color="positive" :disable="!dataset.available"
                  :aria-label="`Select dataset ${dataset.id}`" @click="addDataset(dataset)">
                  <q-tooltip>{{ dataset.available ? 'Select dataset' : 'Dataset unavailable' }}</q-tooltip>
                </q-btn>
              </q-item-section>
            </q-item>
          </q-list>
          <div v-else class="text-grey q-pa-md">{{ datasets.length ? 'No remaining datasets available.' : 'No supported datasets found.' }}</div>
        </q-scroll-area>
      </q-card>
    </template>
    <template #selected>
      <q-card flat bordered>
        <q-scroll-area style="height: 320px">
          <q-list v-if="selectedDatasets.length" separator>
            <q-item v-for="dataset in selectedDatasets" :key="dataset.id">
              <q-item-section>
                <q-item-label class="db-break-word">{{ dataset.id }}</q-item-label>
                <q-item-label caption class="db-break-word">{{ dataset.error || dataset.path || 'Details unavailable' }}</q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-btn flat dense round icon="delete" color="grey-7" :aria-label="`Remove dataset ${dataset.id}`"
                  @click="update({ datasets: selectedIds.filter(id => id !== dataset.id) })">
                  <q-tooltip>Remove dataset</q-tooltip>
                </q-btn>
              </q-item-section>
            </q-item>
          </q-list>
          <div v-else class="text-grey q-pa-md">No datasets selected yet.</div>
        </q-scroll-area>
      </q-card>
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

const props = defineProps({ modelValue: { type: Object, required: true }, agentId: { type: [Number, String], default: null }, agentOnline: Boolean })
const emit = defineEmits(['update:modelValue'])
const agentStore = useAgentStore()
const datasets = ref([])
const loading = ref(false)
const error = ref('')
let loadVersion = 0
const selectedIds = computed(() => props.modelValue.datasets || [])
const availableDatasets = computed(() => datasets.value.filter(dataset => !selectedIds.value.includes(dataset.id)))
const selectedDatasets = computed(() => selectedIds.value.map(id => datasets.value.find(dataset => dataset.id === id) || { id }))
function update(patch) { emit('update:modelValue', { ...props.modelValue, ...patch }) }
function addDataset(dataset) {
  if (dataset.available && !selectedIds.value.includes(dataset.id)) update({ datasets: [...selectedIds.value, dataset.id] })
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
