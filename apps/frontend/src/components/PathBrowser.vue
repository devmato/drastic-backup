<template>
  <q-card flat bordered>
    <div class="column no-wrap" :style="{ height }">
      <div class="row no-wrap items-center col-auto q-px-sm q-py-xs">
        <q-btn v-if="currentPath !== '/'" flat dense round icon="arrow_upward" class="col-auto"
          :color="$q.dark.isActive ? 'grey-5' : 'grey-7'" aria-label="Parent directory" @click="navigate(parentPath)">
          <q-tooltip>Parent directory</q-tooltip>
        </q-btn>
        <nav class="col scroll-x" aria-label="Directory path">
          <div class="row no-wrap items-center">
            <template v-for="(crumb, index) in breadcrumbs" :key="crumb.path">
              <span v-if="index" class="text-grey-6 q-mx-xs" aria-hidden="true">/</span>
              <q-btn v-if="index < breadcrumbs.length - 1" flat dense no-caps no-wrap
                class="col-auto text-caption" :color="$q.dark.isActive ? 'grey-5' : 'grey-7'"
                :icon="index === 0 ? 'home' : undefined" :label="index === 0 ? undefined : crumb.label"
                :aria-label="index === 0 ? 'Root directory' : `Open ${crumb.label}`" @click="navigate(crumb.path)" />
              <span v-else class="col-auto text-caption text-weight-medium text-no-wrap q-px-xs" aria-current="page">
                <q-icon v-if="index === 0" name="home" size="sm" role="img" aria-hidden="false" aria-label="Root directory" />
                <template v-else>{{ crumb.label }}</template>
              </span>
            </template>
          </div>
        </nav>
        <q-spinner v-if="loading" class="col-auto q-ml-xs" size="sm" />
      </div>
      <q-separator />

      <q-scroll-area class="col">
        <q-list v-if="entryRows.length > 0" separator>
          <q-item
            v-for="entry in entryRows"
            :key="entry.path"
            :clickable="entry.navigable"
            @click="openEntry(entry)"
          >
            <q-item-section avatar>
              <q-icon :name="entry.icon || (entry.file ? 'description' : 'folder')" :color="entry.group === 'dataset' || entry.file ? 'blue-grey-6' : 'amber-8'" />
            </q-item-section>
            <q-item-section>
              <q-item-label>{{ entry.name }}</q-item-label>
              <q-item-label caption>
                {{ entryCaption(entry) }}
              </q-item-label>
            </q-item-section>
            <q-item-section side>
              <span class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">{{ formatSize(entry) }}</span>
            </q-item-section>
            <q-item-section v-if="mode !== 'pick-directory'" side>
              <div class="row no-wrap q-gutter-xs">
                <q-btn
                  flat
                  dense
                  round
                  icon="add"
                  :color="entry.state === 'include' ? 'green' : 'grey-7'"
                  :disable="entry.includeDisabled ?? !entry.readable"
                  :aria-label="`Include ${entry.name}`"
                  @click.stop="setInclude(entry)"
                >
                  <q-tooltip>{{ includeTooltip(entry) }}</q-tooltip>
                </q-btn>
                <q-btn
                  v-if="mode === 'include-exclude'"
                  flat
                  dense
                  round
                  icon="remove"
                  :color="entry.state === 'exclude' ? 'red' : 'grey-7'"
                  :disable="entry.excludeDisabled ?? !entry.readable"
                  :aria-label="`Exclude ${entry.name}`"
                  @click.stop="setExclude(entry)"
                >
                  <q-tooltip>Add exclude path</q-tooltip>
                </q-btn>
              </div>
            </q-item-section>
          </q-item>
        </q-list>
        <div v-else-if="!loading" class="text-grey q-pa-md">{{ emptyLabel }}</div>
      </q-scroll-area>
    </div>
  </q-card>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'
import { pathSelectionOptions } from 'src/utils/path-selection'

const props = defineProps({
  loadEntries: { type: Function, required: true },
  mode: { type: String, default: 'include-exclude' },
  selection: {
    type: Object,
    default: () => ({ paths: [], exclude_patterns: [] }),
  },
  selectedPaths: { type: Array, default: () => [] },
  initialPath: { type: String, default: '/' },
  height: { type: String, default: '320px' },
  emptyLabel: { type: String, default: 'No entries found.' },
  errorMessage: { type: String, default: 'Could not load entries' },
  reloadKey: { type: [String, Number, Boolean], default: null },
  entryOptions: { type: Function, default: null },
})
const emit = defineEmits(['update:selection', 'update:selectedPaths', 'pick', 'path-change'])

const $q = useQuasar()
const currentPath = ref(props.initialPath || '/')
const currentPathLabel = ref(currentPath.value)
const loadedBreadcrumbs = ref(null)
const parentPath = ref('/')
const entries = ref([])
const loading = ref(false)
let loadVersion = 0

const selectedPathSet = computed(() => new Set(props.selectedPaths || []))
const breadcrumbs = computed(() => {
  const root = { label: '/', path: '/' }
  if (currentPath.value === '/') return [root]
  // Loaders with opaque source IDs provide targets rather than deriving them from display labels.
  if (loadedBreadcrumbs.value) return [root, ...loadedBreadcrumbs.value]
  if (currentPathLabel.value !== currentPath.value) {
    return [root, { label: currentPathLabel.value, path: currentPath.value }]
  }
  const parts = currentPath.value.split('/').filter(Boolean)
  return [root, ...parts.map((label, index) => ({
    label, path: `/${parts.slice(0, index + 1).join('/')}`,
  }))]
})

const entryRows = computed(() =>
  [...entries.value]
    .map(normalizeEntry)
    .sort((left, right) => {
      if (left.file !== right.file) {
        return Number(left.file) - Number(right.file)
      }
      return left.name.localeCompare(right.name)
    })
)

async function navigate(path = '/') {
  const version = ++loadVersion
  loading.value = true
  try {
    const result = await props.loadEntries(path)
    if (version !== loadVersion) return
    currentPath.value = result.base_directory || result.path || path || '/'
    currentPathLabel.value = result.path_label || currentPath.value
    loadedBreadcrumbs.value = result.breadcrumbs || null
    parentPath.value = result.parent_directory || parentDirectory(currentPath.value)
    entries.value = result.directories || result.entries || []
    emit('path-change', currentPath.value)
  } catch (e) {
    if (version !== loadVersion) return
    entries.value = []
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, props.errorMessage), color: 'red', position: 'top' })
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

function normalizeEntry(entry) {
  const path = entry.path || entryPath(entry.name)
  const file = typeof entry.file === 'boolean' ? entry.file : entry.type !== 'dir'
  const readable = entry.readable !== false
  const name = entry.name || basename(path)
  const row = {
    ...entry,
    name,
    path,
    file,
    readable,
    navigable: entry.navigable ?? (!file && readable),
    group: entry.group || (file ? 'file' : 'folder'),
  }
  const options = props.mode === 'include-only'
    ? { state: selectedPathSet.value.has(path) ? 'include' : null }
    : pathSelectionOptions(row, props.selection)
  return { ...row, ...options, ...props.entryOptions?.({ ...row, ...options }) }
}

function entryPath(name) {
  return currentPath.value === '/' ? `/${name}` : `${currentPath.value}/${name}`
}

function basename(path) {
  const normalized = String(path || '').replace(/\/+$/, '')
  return normalized.split('/').pop() || normalized || '/'
}

function parentDirectory(path) {
  const normalized = String(path || '/').replace(/\/+$/, '') || '/'
  if (normalized === '/') return '/'
  return normalized.split('/').slice(0, -1).join('/') || '/'
}

function openEntry(entry) {
  if (entry.navigable) {
    navigate(entry.path)
  }
}

function setInclude(entry) {
  if (props.mode === 'include-only') {
    const next = selectedPathSet.value.has(entry.path)
      ? props.selectedPaths.filter(path => path !== entry.path)
      : [...props.selectedPaths, entry.path]
    emit('update:selectedPaths', next)
    return
  }

  setPath(entry, false)
}

function setExclude(entry) {
  setPath(entry, true)
}

function setPath(entry, exclude) {
  const path = entry.path
  const nextIncludes = (props.selection.paths || []).filter(entry => entry.path !== path)
  const nextExcludes = (props.selection.exclude_patterns || []).filter(entry => entry.path !== path)
  const targetEntry = { ...entry.selectionEntry, path, group: entry.group || (entry.file ? 'file' : 'folder') }

  if (exclude) {
    nextExcludes.push(targetEntry)
  } else {
    nextIncludes.push(targetEntry)
  }

  emit('update:selection', {
    paths: nextIncludes,
    exclude_patterns: nextExcludes,
  })
}

function entryCaption(entry) {
  if (entry.note || entry.caption) return entry.note || entry.caption
  if (!entry.readable) return 'Not readable'
  if (props.mode === 'include-only') return entry.file ? 'File in snapshot' : 'Directory in snapshot'
  return entry.file ? 'Readable file' : 'Readable directory'
}

function includeTooltip(entry) {
  if (entry.includeTooltip) return entry.includeTooltip
  if (props.mode === 'include-only' && selectedPathSet.value.has(entry.path)) {
    return 'Remove from restore'
  }
  return props.mode === 'include-only' ? 'Add to restore' : 'Add include path'
}

function formatSize(entry) {
  if (!entry.file || entry.size === undefined || entry.size === null) {
    return ''
  }
  return props.mode === 'include-only' ? formatBytes(entry.size) : `${entry.size} MB`
}

function formatBytes(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = Math.abs(number)
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  return `${size.toLocaleString(undefined, { maximumFractionDigits: unitIndex === 0 ? 0 : 1 })} ${units[unitIndex]}`
}

watch(
  () => [props.initialPath, props.reloadKey],
  () => {
    currentPath.value = props.initialPath || '/'
    navigate(currentPath.value)
  },
  { immediate: true }
)

defineOptions({ name: 'PathBrowser' })
</script>
