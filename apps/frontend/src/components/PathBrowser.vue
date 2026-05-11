<template>
  <div>
    <div class="row items-center q-gutter-sm q-mb-xs">
      <q-btn v-if="currentPath !== '/'" flat dense round icon="arrow_upward" @click="navigate(parentPath)">
        <q-tooltip>Parent directory</q-tooltip>
      </q-btn>
      <q-chip dense square color="grey-2" text-color="dark" class="col">
        <span class="ellipsis">{{ currentPath }}</span>
      </q-chip>
      <q-spinner v-if="loading" size="sm" />
    </div>

    <q-card flat bordered>
      <q-scroll-area :style="{ height }">
        <q-list v-if="entryRows.length > 0" separator>
          <q-item
            v-for="entry in entryRows"
            :key="entry.path"
            :clickable="entry.navigable"
            :disable="!entry.readable"
            @click="openEntry(entry)"
          >
            <q-item-section avatar>
              <q-icon :name="entry.file ? 'description' : 'folder'" :color="entry.file ? 'blue-grey-6' : 'amber-8'" />
            </q-item-section>
            <q-item-section>
              <q-item-label>{{ entry.name }}</q-item-label>
              <q-item-label caption>
                {{ entryCaption(entry) }}
              </q-item-label>
            </q-item-section>
            <q-item-section side>
              <span class="text-caption text-grey-7">{{ formatSize(entry) }}</span>
            </q-item-section>
            <q-item-section v-if="mode !== 'pick-directory'" side>
              <div class="row no-wrap q-gutter-xs">
                <q-btn
                  flat
                  dense
                  round
                  icon="add"
                  :color="entry.state === 'include' ? 'green' : 'grey-7'"
                  :disable="!entry.readable"
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
                  :disable="!entry.readable"
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
    </q-card>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

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
})
const emit = defineEmits(['update:selection', 'update:selectedPaths', 'pick', 'path-change'])

const $q = useQuasar()
const currentPath = ref(props.initialPath || '/')
const parentPath = ref('/')
const entries = ref([])
const loading = ref(false)

const includePathSet = computed(() => new Set((props.selection.paths || []).map(entry => entry.path)))
const excludePathSet = computed(() => new Set((props.selection.exclude_patterns || []).map(entry => entry.path)))
const selectedPathSet = computed(() => new Set(props.selectedPaths || []))

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
  loading.value = true
  try {
    const result = await props.loadEntries(path)
    currentPath.value = result.base_directory || result.path || path || '/'
    parentPath.value = result.parent_directory || parentDirectory(currentPath.value)
    entries.value = result.directories || result.entries || []
    emit('path-change', currentPath.value)
  } catch (e) {
    entries.value = []
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, props.errorMessage), color: 'red', position: 'top' })
  } finally {
    loading.value = false
  }
}

function normalizeEntry(entry) {
  const path = entry.path || entryPath(entry.name)
  const file = typeof entry.file === 'boolean' ? entry.file : entry.type !== 'dir'
  const readable = entry.readable !== false
  const name = entry.name || basename(path)
  return {
    ...entry,
    name,
    path,
    file,
    readable,
    navigable: !file && readable,
    state: includeState(path),
  }
}

function includeState(path) {
  if (props.mode === 'include-only') {
    return selectedPathSet.value.has(path) ? 'include' : null
  }
  return includePathSet.value.has(path) ? 'include' : excludePathSet.value.has(path) ? 'exclude' : null
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

  setPath(entry.path, false, entry.file ? 'file' : 'folder')
}

function setExclude(entry) {
  setPath(entry.path, true, entry.file ? 'file' : 'folder')
}

function setPath(path, exclude, group) {
  const nextIncludes = (props.selection.paths || []).filter(entry => entry.path !== path)
  const nextExcludes = (props.selection.exclude_patterns || []).filter(entry => entry.path !== path)
  const targetEntry = { path, group }

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
  if (!entry.readable) return 'Not readable'
  if (props.mode === 'include-only') return entry.file ? 'File in snapshot' : 'Directory in snapshot'
  return entry.file ? 'Readable file' : 'Readable directory'
}

function includeTooltip(entry) {
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
