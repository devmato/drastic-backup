<template>
  <q-dialog v-model="dialogVisible" persistent>
    <q-card class="db-dialog-card-lg">
      <q-card-section class="row items-center no-wrap">
        <h2 class="text-h6 q-my-none">{{ kind ? 'Edit Connection' : 'Add Connection' }}</h2>
        <q-space />
        <q-btn flat dense round icon="close" aria-label="Close connection dialog" :disable="busy" @click="dialogVisible = false" />
      </q-card-section>
      <q-card-section class="q-pt-none">
        <q-select
          v-if="!kind"
          v-model="selectedKind"
          :options="availableTypes"
          outlined
          :dense="!$q.platform.has.touch"
          emit-value map-options
          label="Connection type"
          :disable="busy || !agent.online"
          class="q-mb-md"
        />
        <component
          :is="selectedKind === 'proxmox' ? AgentProxmoxSettings : AgentTrueNASSettings"
          v-if="selectedKind"
          :key="selectedKind"
          ref="settingsForm"
          :agent-id="agent.id"
          :agent-online="agent.online"
          @saved="onSaved"
          @cleaned-up="emit('updated')"
        >
          <template #actions>
            <q-btn flat no-caps label="Cancel" :disable="busy" @click="dialogVisible = false" />
          </template>
        </component>
      </q-card-section>
      <q-card-actions v-if="!selectedKind" align="right" class="q-pa-md">
        <q-btn flat no-caps label="Cancel" @click="dialogVisible = false" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import AgentProxmoxSettings from 'components/agents/AgentProxmoxSettings.vue'
import AgentTrueNASSettings from 'components/agents/AgentTrueNASSettings.vue'
import { getAvailableConnectionTypes } from 'src/utils/agent-connections'

const props = defineProps({
  agent: { type: Object, required: true },
  kind: { type: String, default: null },
})
const dialogVisible = defineModel({ type: Boolean, required: true })
const emit = defineEmits(['updated'])
const selectedKind = ref(props.kind || null)
const settingsForm = ref(null)
const busy = computed(() => Boolean(settingsForm.value?.busy))
const availableTypes = computed(() => getAvailableConnectionTypes(props.agent))

function onSaved() {
  dialogVisible.value = false
  emit('updated')
}
</script>
