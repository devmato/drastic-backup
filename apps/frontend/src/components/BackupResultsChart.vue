<template>
  <div>
    <div class="flex flex-center q-mb-md">
      <svg viewBox="0 0 200 200" width="200" height="200" class="db-results-donut" :role="jobs.length ? 'group' : 'img'" :aria-label="jobs.length ? 'Current backup status. Latest result per job. Select a segment to view matching jobs.' : 'No backup jobs configured yet. No backup results available.'">
        <template v-if="jobs.length === 0">
          <circle cx="100" cy="100" r="75" fill="none" stroke="currentColor" :class="$q.dark.isActive ? 'text-grey-8' : 'text-grey-3'" stroke-width="30" />
          <text x="100" y="100" text-anchor="middle" dominant-baseline="central" font-size="14" fill="currentColor" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">No jobs yet</text>
        </template>
        <template v-for="segment in segments.filter(item => item.count > 0)" :key="segment.value">
          <router-link :to="{ path: '/jobs', query: { last_state: segment.value } }" custom v-slot="{ href, navigate }">
            <a :href="href" @click="navigate" :aria-label="`${segment.label}: ${segment.count} jobs (${percentage(segment)}). View matching jobs.`">
              <title>{{ segment.label }}: {{ segment.count }} ({{ percentage(segment) }})</title>
              <circle
                cx="100" cy="100" r="75" fill="none" pathLength="100"
                stroke="currentColor" :class="`text-${segment.color}`" stroke-width="30"
                :stroke-dasharray="`${segment.fraction * 100} ${100 - segment.fraction * 100}`"
                :stroke-dashoffset="-segment.start * 100" transform="rotate(-90 100 100)"
              />
              <text v-if="segment.fraction >= 0.15" :x="segment.labelX" :y="segment.labelY" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="500" fill="currentColor" class="text-grey-9" aria-hidden="true">{{ segment.count }}</text>
            </a>
          </router-link>
        </template>
      </svg>
    </div>
    <div v-if="jobs.length" class="q-pa-xs rounded-borders" :class="$q.dark.isActive ? 'bg-grey-10' : 'bg-grey-1'">
      <div class="row justify-center q-gutter-xs">
        <q-btn v-for="segment in segments" :key="segment.value" flat dense no-caps no-wrap :to="{ path: '/jobs', query: { last_state: segment.value } }" :aria-label="`${segment.label}: ${segment.count} jobs (${percentage(segment)}). View matching jobs.`" class="col-auto">
          <q-icon name="circle" :color="segment.count ? segment.color : 'grey-5'" size="xs" class="q-mr-xs" />
          {{ segment.label }}
          <q-tooltip>{{ segment.count }} jobs · {{ percentage(segment) }}</q-tooltip>
        </q-btn>
      </div>
    </div>
    <p v-if="jobs.length === 0" class="text-caption text-center q-mt-md q-mb-none" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Results will appear after you configure a backup job.</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { summarizeBackupResults } from 'src/utils/backup-results'

const props = defineProps({ jobs: { type: Array, required: true } })
const segments = computed(() => summarizeBackupResults(props.jobs).map(segment => {
  const angle = (segment.start + segment.fraction / 2) * Math.PI * 2 - Math.PI / 2
  return { ...segment, labelX: 100 + 75 * Math.cos(angle), labelY: 100 + 75 * Math.sin(angle) }
}))
function percentage(segment) {
  return `${Number((segment.fraction * 100).toFixed(1))}%`
}
</script>

<style scoped>
.db-results-donut {
  max-width: 100%;
  height: auto;
}

.db-results-donut a:hover {
  opacity: .85;
}

.db-results-donut a:focus-visible {
  outline: 2px solid currentColor;
  outline-offset: 2px;
}
</style>
