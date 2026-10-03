<template>
  <q-btn flat dense round :icon="icon" :color="color" :aria-label="label">
    <q-tooltip>{{ label }}</q-tooltip>
  </q-btn>
</template>

<script setup>
defineProps({
  icon: { type: String, required: true },
  label: { type: String, required: true },
  color: { type: String, default: 'primary' },
})
</script>
