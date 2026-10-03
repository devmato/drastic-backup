<template>
  <header class="q-mb-md">
    <div v-if="$slots.breadcrumbs" class="q-mb-sm">
      <slot name="breadcrumbs" />
    </div>
    <div class="row items-center q-col-gutter-md">
      <div class="col-12 col-sm">
        <h1 class="db-page-title text-h5 text-weight-medium q-my-none">{{ title }}</h1>
        <div v-if="description" class="text-body2 q-mt-xs" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
          {{ description }}
        </div>
      </div>
      <div v-if="$slots.actions" class="col-12 col-sm-auto">
        <div class="row items-center q-gutter-sm">
          <slot name="actions" />
        </div>
      </div>
    </div>
  </header>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
})
</script>
