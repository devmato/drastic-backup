<template>
  <div class="full-width column items-center text-center q-pa-md" role="status">
    <q-icon :name="icon" size="sm" color="grey-6" class="q-mb-sm" />
    <h2 class="text-subtitle1 text-weight-medium q-my-none">{{ title }}</h2>
    <p class="text-body2 q-mt-xs q-mb-none" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">{{ description }}</p>
    <div v-if="$slots.default" class="q-mt-md">
      <slot />
    </div>
  </div>
</template>

<script setup>
defineProps({
  icon: { type: String, required: true },
  title: { type: String, required: true },
  description: { type: String, required: true },
})
</script>
