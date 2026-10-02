<template>
  <div class="q-gutter-md">
    <div class="row items-center q-gutter-sm">
      <div class="text-subtitle1">TrueNAS datasets</div>
      <q-space />
      <q-btn flat dense icon="settings" label="Connection" :to="`/agents/${agentId}?tab=connections`" target="_blank">
        <q-tooltip>Opens in a new tab; your job draft stays here.</q-tooltip>
      </q-btn>
      <q-btn flat dense icon="refresh" label="Refresh" :loading="loading" :disable="!agentOnline" @click="loadDatasets" />
    </div>
    <q-banner v-if="!agentOnline">Bring the agent online to discover datasets.</q-banner>
    <q-banner v-if="error" class="bg-negative text-white">{{ error }}</q-banner>
    <q-select outlined :model-value="modelValue.datasets || []" :options="options" label="Datasets" multiple use-chips emit-value map-options :loading="loading" @update:model-value="value => update({ datasets: value })" />
    <div class="text-caption">Select each dataset explicitly, including child datasets. Each selected dataset gets its own temporary ZFS snapshot and file backup. Snapshots are removed after the run.</div>
    <q-list v-if="datasets.length" bordered separator>
      <q-item v-for="dataset in datasets" :key="dataset.id">
        <q-item-section>
          <q-item-label>{{ dataset.id }}</q-item-label>
          <q-item-label caption>{{ dataset.error || dataset.path }}</q-item-label>
        </q-item-section>
        <q-item-section side><q-icon :name="dataset.available ? 'check_circle' : 'warning'" :color="dataset.available ? 'positive' : 'warning'" :aria-label="dataset.available ? 'Available' : 'Unavailable'" /></q-item-section>
      </q-item>
    </q-list>
    <q-input outlined type="textarea" autogrow label="Exclude patterns (one per line, relative to each dataset)" :model-value="(modelValue.exclude_patterns || []).join('\n')" @update:model-value="value => update({ exclude_patterns: value.split('\n').filter(Boolean) })" />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useAgentStore } from 'stores/agent'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const props = defineProps({ modelValue: { type: Object, required: true }, agentId: { type: [Number, String], default: null }, agentOnline: Boolean })
const emit = defineEmits(['update:modelValue'])
const agentStore = useAgentStore()
const datasets = ref([])
const loading = ref(false)
const error = ref('')
let loadVersion = 0
const options = computed(() => datasets.value.map(dataset => ({ label: dataset.id, value: dataset.id, disable: !dataset.available })))
function update(patch) { emit('update:modelValue', { ...props.modelValue, ...patch }) }

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
