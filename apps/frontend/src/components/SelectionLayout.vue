<template>
  <div>
    <div v-if="$slots.header" class="row items-center q-gutter-sm q-mb-sm">
      <slot name="header" />
    </div>
    <div v-if="$slots.options" class="q-mb-md">
      <slot name="options" />
    </div>
    <div class="row q-col-gutter-sm items-stretch">
      <div class="col-12 col-lg-6">
        <div class="text-subtitle2 q-mb-xs">{{ availableTitle }}</div>
        <slot name="available" />
      </div>
      <div class="col-12 col-lg-6">
        <div class="text-subtitle2 q-mb-xs">{{ selectedTitle }}</div>
        <slot name="selected" />
      </div>
    </div>
    <template v-if="$slots.advanced">
      <div class="row justify-end q-mt-sm">
        <q-btn flat dense no-caps label="Advanced" :icon-right="advancedOpen || advancedInvalid ? 'expand_less' : 'expand_more'"
          :aria-expanded="advancedOpen || advancedInvalid" :disable="advancedInvalid" @click="advancedOpen = !advancedOpen" />
      </div>
      <div v-if="advancedOpen || advancedInvalid" class="q-mt-sm">
        <slot name="advanced" />
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  availableTitle: { type: String, required: true },
  selectedTitle: { type: String, required: true },
  advancedInvalid: Boolean,
})
const advancedOpen = ref(false)
</script>
