<template>
  <div class="q-gutter-y-sm">
    <q-toggle v-model="retry.enabled" label="Retry temporary backup failures" />
    <template v-if="retry.enabled">
      <q-input v-model.number="retry.max_attempts" outlined dense type="number" label="Maximum attempts in total" hint="Includes the first attempt. Each attempt runs the whole job, including its commands." :min="1" :max="6" :rules="[value => Number.isInteger(value) && value >= 1 && value <= 6 || 'Use 1–6 attempts']" />
      <q-input v-model.number="retry.delay_minutes" outlined dense type="number" label="Wait between attempts (minutes)" :min="1" :max="1440" :rules="[value => Number.isInteger(value) && value >= 1 && value <= 1440 || 'Use 1–1440 minutes']" />
      <div class="text-caption">Requires agent protocol 18 or newer. Permanent errors and cancelled backups are not retried.</div>
    </template>
  </div>
</template>

<script setup>
const retry = defineModel({ type: Object, required: true })
</script>
