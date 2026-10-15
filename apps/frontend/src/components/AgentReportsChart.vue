<template>
  <div class="column">
    <div class="flex flex-center q-mb-md">
      <svg viewBox="0 0 200 200" width="150" height="150" class="db-results-donut" :role="total ? 'group' : 'img'" :aria-label="total ? `${label}. Select a segment to view matching reports.` : `${label}. No reports in this period.`">
        <template v-if="total === 0">
          <circle cx="100" cy="100" r="85" fill="none" stroke="currentColor" :class="$q.dark.isActive ? 'text-grey-8' : 'text-grey-3'" stroke-width="30" />
          <text x="100" y="100" text-anchor="middle" dominant-baseline="central" font-size="14" fill="currentColor" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">No reports</text>
        </template>
        <template v-for="segment in chartSegments.filter(item => item.count > 0)" :key="segment.value">
          <router-link :to="segment.to" custom v-slot="{ href, navigate }">
            <a :href="href" @click="navigate" :aria-label="`${segment.label}: ${segment.count} reports. View matching reports.`">
              <title>{{ segment.label }}: {{ segment.count }} ({{ percentage(segment) }})</title>
              <circle
                cx="100" cy="100" r="85" fill="none" pathLength="100"
                stroke="currentColor" :class="`text-${segment.color}`" stroke-width="30"
                :stroke-dasharray="`${segment.fraction * 100} ${100 - segment.fraction * 100}`"
                :stroke-dashoffset="-segment.start * 100" transform="rotate(-90 100 100)"
              />
              <text v-if="segment.fraction >= 0.08" :x="segment.labelX" :y="segment.labelY" text-anchor="middle" dominant-baseline="central" font-size="16" font-weight="500" fill="currentColor" class="text-grey-9" aria-hidden="true">{{ segment.count }}</text>
            </a>
          </router-link>
        </template>
        <text v-if="total" x="100" y="100" text-anchor="middle" dominant-baseline="central" font-size="18" font-weight="500" fill="currentColor" aria-hidden="true">{{ total }}</text>
      </svg>
    </div>
    <div class="q-pa-sm q-mt-auto rounded-borders" :class="$q.dark.isActive ? 'bg-grey-10' : 'bg-grey-1'">
      <div class="row justify-center q-gutter-xs">
        <q-btn v-for="segment in chartSegments" :key="segment.value" flat dense no-caps no-wrap :to="segment.to" :aria-label="`${segment.label}: ${segment.count} reports. View matching reports.`" class="col-auto">
          <q-icon name="circle" :color="segment.color" size="xs" class="q-mr-xs" />
          {{ segment.label }}
          <q-tooltip>{{ segment.count }} reports · {{ percentage(segment) }}</q-tooltip>
        </q-btn>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ segments: { type: Array, required: true }, label: { type: String, required: true } })
const total = computed(() => props.segments.reduce((sum, segment) => sum + segment.count, 0))
const chartSegments = computed(() => {
  let start = 0
  return props.segments.map(segment => {
    const fraction = total.value ? segment.count / total.value : 0
    const angle = (start + fraction / 2) * Math.PI * 2 - Math.PI / 2
    const result = { ...segment, fraction, start, labelX: 100 + 85 * Math.cos(angle), labelY: 100 + 85 * Math.sin(angle) }
    start += fraction
    return result
  })
})
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
