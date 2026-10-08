<template>
  <div>
    <div v-if="$slots.header" class="row items-center q-gutter-sm q-mb-sm">
      <slot name="header" />
    </div>
    <div class="row q-col-gutter-sm items-stretch">
      <div class="col-12 col-lg-6">
        <div class="text-subtitle2 q-mb-xs">{{ availableTitle }}</div>
        <slot name="available">
          <SelectionList :entries="availableEntries" :actions="availableActions" :height="height"
            :message="availableMessage" :error="availableError" :loading="loading"
            :loading-label="loadingLabel" :empty-label="emptyAvailableLabel"
            @action="(action, entry) => emit(action, entry)" />
        </slot>
      </div>
      <div class="col-12 col-lg-6">
        <div class="text-subtitle2 q-mb-xs">{{ selectedTitle }}</div>
        <slot name="selected">
          <SelectionList :entries="selectedEntries" :actions="removeActions" :height="height"
            :empty-label="emptySelectedLabel" @action="(action, entry) => emit(action, entry)" />
        </slot>
      </div>
    </div>
    <div v-if="$slots.options" class="q-mt-md">
      <slot name="options" />
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
import SelectionList from 'components/SelectionList.vue'

defineProps({
  availableTitle: { type: String, required: true },
  selectedTitle: { type: String, required: true },
  availableEntries: { type: Array, default: () => [] },
  selectedEntries: { type: Array, default: () => [] },
  availableActions: { type: Array, default: () => [{ name: 'select', icon: 'add', color: 'positive', label: 'Select' }] },
  availableMessage: { type: String, default: '' },
  availableError: { type: String, default: '' },
  loading: Boolean,
  loadingLabel: { type: String, default: 'Loading…' },
  emptyAvailableLabel: { type: String, default: 'No entries available.' },
  emptySelectedLabel: { type: String, default: 'No entries selected yet.' },
  height: { type: String, default: '320px' },
  advancedInvalid: Boolean,
})
const emit = defineEmits(['select', 'exclude', 'remove'])
const removeActions = [{ name: 'remove', icon: 'delete', color: 'grey-7', label: 'Remove rule for' }]
const advancedOpen = ref(false)
</script>
