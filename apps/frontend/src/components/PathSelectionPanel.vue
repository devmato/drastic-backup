<template>
  <SelectionLayout :available-title="browserTitle" :selected-title="selectedTitle">
    <template v-if="$slots.header" #header><slot name="header" /></template>
    <template #available>
      <PathBrowser
        :mode="mode"
        :load-entries="loadEntries"
        :selection="selection"
        :selected-paths="selectedPaths"
        :reload-key="reloadKey"
        :height="height"
        :empty-label="emptyLabel"
        :error-message="errorMessage"
        @update:selection="emit('update:selection', $event)"
        @update:selected-paths="emit('update:selectedPaths', $event)"
      />
    </template>

    <template #selected>
      <q-card flat bordered>
        <q-scroll-area :style="{ height }">
          <q-list v-if="selectedEntries.length > 0" separator>
            <q-item v-for="entry in selectedEntries" :key="entry.path">
              <q-item-section avatar>
                <q-icon :name="entry.exclude ? 'remove_circle' : 'add_circle'" :color="entry.exclude ? 'red' : 'green'" />
              </q-item-section>
              <q-item-section avatar>
                <q-icon :name="groupIcon(entry.group)" color="blue-grey-7" />
              </q-item-section>
              <q-item-section>
                <q-item-label>{{ entry.path }}</q-item-label>
                <q-item-label caption>{{ entry.exclude ? 'Exclude' : selectedCaption }}</q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-btn flat dense round icon="delete" color="grey-7" @click="removePath(entry.path)" />
              </q-item-section>
            </q-item>
          </q-list>
          <div v-else class="text-grey q-pa-md">{{ emptySelectedLabel }}</div>
        </q-scroll-area>
      </q-card>
    </template>
    <template v-if="$slots.advanced" #advanced><slot name="advanced" /></template>
  </SelectionLayout>
</template>

<script setup>
import { computed } from 'vue'
import PathBrowser from 'components/PathBrowser.vue'
import SelectionLayout from 'components/SelectionLayout.vue'

const props = defineProps({
  mode: { type: String, default: 'include-exclude' },
  loadEntries: { type: Function, required: true },
  selection: {
    type: Object,
    default: () => ({ paths: [], exclude_patterns: [] }),
  },
  selectedPaths: { type: Array, default: () => [] },
  reloadKey: { type: [String, Number, Boolean], default: null },
  browserTitle: { type: String, default: 'Directory Browser' },
  selectedTitle: { type: String, default: 'Selected Paths' },
  selectedCaption: { type: String, default: 'Include' },
  emptyLabel: { type: String, default: 'No entries found.' },
  emptySelectedLabel: { type: String, default: 'No paths selected yet.' },
  errorMessage: { type: String, default: 'Could not load entries' },
  height: { type: String, default: '320px' },
})
const emit = defineEmits(['update:selection', 'update:selectedPaths'])

const selectedEntries = computed(() => {
  if (props.mode === 'include-only') {
    return (props.selectedPaths || []).map(path => ({ path, exclude: false, group: 'file' }))
  }

  return [
    ...(props.selection.paths || []).map(entry => ({ ...entry, exclude: false })),
    ...(props.selection.exclude_patterns || []).map(entry => ({ ...entry, exclude: true })),
  ]
})

function removePath(path) {
  if (props.mode === 'include-only') {
    emit('update:selectedPaths', props.selectedPaths.filter(candidate => candidate !== path))
    return
  }

  emit('update:selection', {
    paths: (props.selection.paths || []).filter(entry => entry.path !== path),
    exclude_patterns: (props.selection.exclude_patterns || []).filter(entry => entry.path !== path),
  })
}

function groupIcon(group) {
  if (group === 'file') return 'description'
  if (group === 'pattern') return 'rule'
  return 'folder'
}

defineOptions({ name: 'PathSelectionPanel' })
</script>
