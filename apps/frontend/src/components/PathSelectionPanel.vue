<template>
  <SelectionLayout :available-title="browserTitle" :selected-title="selectedTitle"
    :selected-entries="selectionRows" :empty-selected-label="emptySelectedLabel" :height="height"
    :available-message="availableMessage" :advanced-invalid="advancedInvalid"
    @remove="entry => removePath(entry.path)">
    <template v-if="$slots.header" #header><slot name="header" /></template>
    <template v-if="!availableMessage" #available>
      <PathBrowser
        :mode="mode"
        :load-entries="loadEntries"
        :selection="selection"
        :selected-paths="selectedPaths"
        :reload-key="reloadKey"
        :entry-options="entryOptions"
        :height="height"
        :empty-label="emptyLabel"
        :error-message="errorMessage"
        @update:selection="emit('update:selection', $event)"
        @update:selected-paths="emit('update:selectedPaths', $event)"
      />
    </template>

    <template v-if="$slots.options" #options><slot name="options" /></template>
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
  entryOptions: { type: Function, default: null },
  browserTitle: { type: String, default: 'Directory Browser' },
  selectedTitle: { type: String, default: 'Selected Paths' },
  selectedCaption: { type: String, default: 'Include' },
  emptyLabel: { type: String, default: 'No entries found.' },
  availableMessage: { type: String, default: '' },
  emptySelectedLabel: { type: String, default: 'No paths selected yet.' },
  errorMessage: { type: String, default: 'Could not load entries' },
  height: { type: String, default: '320px' },
  advancedInvalid: Boolean,
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
const selectionRows = computed(() => selectedEntries.value.map(entry => ({
  ...entry,
  id: `${entry.exclude ? 'exclude' : 'include'}:${entry.path}`,
  label: entry.label || entry.path,
  caption: entry.exclude ? 'Exclude' : props.selectedCaption,
  state: entry.exclude ? 'exclude' : 'include',
  icon: groupIcon(entry.group),
})))

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
  if (group === 'dataset') return 'storage'
  if (group === 'host') return 'dns'
  if (group === 'vm') return 'computer'
  if (group === 'pattern') return 'rule'
  return 'folder'
}

defineOptions({ name: 'PathSelectionPanel' })
</script>
