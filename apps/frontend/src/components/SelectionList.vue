<template>
  <q-card flat bordered>
    <q-scroll-area :style="{ height }">
      <q-banner v-if="message">{{ message }}</q-banner>
      <q-banner v-else-if="error" class="bg-negative text-white" role="alert">{{ error }}</q-banner>
      <div v-else-if="loading" class="text-grey q-pa-md" role="status">{{ loadingLabel }}</div>
      <q-list v-else-if="entries.length" separator>
        <q-item v-for="entry in entries" :key="entry.id">
          <q-item-section v-if="entry.state" avatar>
            <q-icon :name="entry.state === 'exclude' ? 'remove_circle' : 'add_circle'"
              :color="entry.state === 'exclude' ? 'negative' : 'positive'" />
          </q-item-section>
          <q-item-section v-if="entry.icon" avatar>
            <q-icon :name="entry.icon" color="blue-grey-7" />
          </q-item-section>
          <q-item-section>
            <q-item-label class="db-break-word">{{ entry.label }}</q-item-label>
            <q-item-label v-if="entry.caption" caption class="db-break-word">{{ entry.caption }}</q-item-label>
            <q-item-label v-if="entry.note" caption class="db-break-word">{{ entry.note }}</q-item-label>
          </q-item-section>
          <q-item-section side>
            <div class="row no-wrap q-gutter-xs">
              <q-btn v-for="action in entry.actions || actions" :key="action.name" flat dense round
                :icon="action.icon" :color="action.color" :disable="action.disable"
                :aria-label="`${action.label} ${entry.actionLabel || entry.label}`" @click="emit('action', action.name, entry)">
                <q-tooltip>{{ action.tooltip || action.label }}</q-tooltip>
              </q-btn>
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <div v-else class="text-grey q-pa-md">{{ emptyLabel }}</div>
    </q-scroll-area>
  </q-card>
</template>

<script setup>
defineProps({
  entries: { type: Array, default: () => [] },
  actions: { type: Array, default: () => [] },
  height: { type: String, default: '320px' },
  message: { type: String, default: '' },
  error: { type: String, default: '' },
  loading: Boolean,
  loadingLabel: { type: String, default: 'Loading…' },
  emptyLabel: { type: String, default: 'No entries.' },
})
const emit = defineEmits(['action'])
</script>
