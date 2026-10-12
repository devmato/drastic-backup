<template>
  <q-card bordered flat class="col-auto column no-wrap overflow-hidden" style="height: 350px">
    <q-card-section class="col-auto">
      <div class="row items-center no-wrap q-col-gutter-sm">
        <div class="col-auto">
          <q-icon :name="card.icon" size="32px" color="primary" />
        </div>
        <div class="col">
          <h2 class="text-subtitle1 text-weight-medium q-my-none">{{ card.title }}</h2>
          <div class="row q-gutter-xs q-mt-xs">
            <q-chip v-for="chip in card.chips" :key="chip" dense square color="grey-2" text-color="dark">
              {{ chip }}
            </q-chip>
          </div>
        </div>
      </div>
    </q-card-section>

    <q-card-section class="col-auto q-pt-none">
      <q-tabs
        :class="$q.dark.isActive ? 'text-grey-3' : 'text-grey-9'"
        :model-value="activeAction.id"
        dense
        no-caps
        active-color="primary"
        indicator-color="primary"
        align="left"
        :breakpoint="0"
        narrow-indicator
        @update:model-value="selectedActionId = $event"
      >
        <q-tab v-for="action in card.actions" :id="`${card.id}-${action.id}-tab`" :key="action.id" :name="action.id" :label="action.label" />
      </q-tabs>
    </q-card-section>

    <q-separator class="col-auto" />

    <q-card-section class="col column no-wrap" role="tabpanel" :aria-labelledby="`${card.id}-${activeAction.id}-tab`">
      <div class="col-auto text-subtitle2">{{ activeAction.description }}</div>
      <div class="col-auto text-body2 q-mt-xs" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">{{ activeAction.note }}</div>
      <q-card flat bordered class="col column no-wrap q-mt-sm">
        <q-card-section class="col-auto row items-center q-pa-sm">
          <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Install snippet</div>
          <q-space />
          <q-btn dense flat color="primary" icon="content_copy" label="Copy" no-caps @click="emit('copy', activeAction)" />
        </q-card-section>
        <q-separator class="col-auto" />
        <q-scroll-area
          :key="activeAction.id"
          class="col"
          :class="$q.dark.isActive ? 'bg-grey-9' : 'bg-grey-2'"
          tabindex="0"
          role="region"
          :aria-label="`${card.title}: ${activeAction.label} install command`"
        >
          <pre class="db-code-block db-code-block--wrap q-ma-none overflow-hidden" style="max-height: none">{{ activeAction.snippet }}</pre>
        </q-scroll-area>
      </q-card>
    </q-card-section>
  </q-card>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  card: { type: Object, required: true },
})
const emit = defineEmits(['copy'])
const selectedActionId = ref('')
// The page supplies at least one action; fall back when a development-only action disappears.
const activeAction = computed(() => props.card.actions.find(action => action.id === selectedActionId.value) || props.card.actions[0])
</script>
