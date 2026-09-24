<template>
  <q-page class="q-pa-md">
    <q-breadcrumbs class="q-pb-md">
      <q-breadcrumbs-el label="Agents" icon="desktop_windows" to="/agents" />
      <q-breadcrumbs-el label="Add Agent" />
    </q-breadcrumbs>

    <div class="q-mb-md">
      <div class="row items-center q-col-gutter-md q-row-gutter-sm">
      <div class="text-h5">Add Agent</div>
      </div>
      <div class="text-body2 text-grey-7 q-mt-sm">
        Install the Linux agent directly from Git or run the Docker agent.
        Copy the install snippet for the selected target.
      </div>
    </div>

    <q-banner v-if="loadError" rounded class="bg-red-1 text-red-10 q-mb-md">
      {{ loadError }}
      <template #action>
        <q-btn flat color="red" label="Retry" @click="loadInstallOptions" />
      </template>
    </q-banner>

    <div v-if="loading" class="row justify-center q-py-xl">
      <q-spinner color="primary" size="40px" />
    </div>

    <div v-else class="row q-col-gutter-md q-row-gutter-md">
      <div v-for="card in platformCards" :key="card.id" class="col-12 col-md-6 col-xl-3">
        <q-card bordered flat class="full-height column no-wrap">
          <q-card-section class="col-auto">
            <div class="row items-center no-wrap q-col-gutter-sm">
              <div class="col-auto">
                <q-icon :name="card.icon" size="32px" color="primary" />
              </div>
              <div class="col">
                <div class="text-h6">{{ card.title }}</div>
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
              :model-value="selectedActionId(card)"
              dense
              no-caps
              active-color="primary"
              indicator-color="primary"
              align="left"
              narrow-indicator
              @update:model-value="setSelectedAction(card, $event)"
            >
              <q-tab v-for="action in card.actions" :key="action.id" :name="action.id" :label="action.label" />
            </q-tabs>
          </q-card-section>

          <q-separator />

          <q-card-section class="col-auto">
            <q-tab-panels :model-value="selectedActionId(card)" animated class="bg-transparent q-pa-none">
              <q-tab-panel v-for="action in card.actions" :key="action.id" :name="action.id" class="q-pa-none">
                <div class="text-subtitle2">{{ action.description }}</div>
                <div class="text-body2 text-grey-7 q-mt-xs">{{ action.note }}</div>
                <q-card flat bordered class="q-mt-sm">
                  <q-card-section class="row items-center q-pa-sm">
                    <div class="text-caption text-grey-7">Install snippet</div>
                    <q-space />
                    <q-btn dense flat color="primary" icon="content_copy" label="Copy" no-caps @click="copyAction(action)" />
                  </q-card-section>
                  <q-separator />
                  <q-card-section class="q-pa-none">
                    <pre class="db-code-block db-code-block--wrap text-dark q-ma-none">{{ buildActionSnippet(action) }}</pre>
                  </q-card-section>
                </q-card>
              </q-tab-panel>
            </q-tab-panels>
          </q-card-section>
        </q-card>
      </div>
    </div>
  </q-page>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { copyToClipboard, useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { useUserStore } from 'stores/user'
import { shouldIgnoreApiError } from 'src/utils/api-error'

const $q = useQuasar()
const agentStore = useAgentStore()
const userStore = useUserStore()

const loading = ref(false)
const loadError = ref('')
const installOptions = ref({ server_url: '', targets: [] })
const selectedActions = ref({})
const showDevelopTab = computed(() => ['dev', 'test'].includes(userStore.environment))
const publicBaseUrl = computed(() => (installOptions.value.server_url || window.location.origin).replace(/\/$/, ''))

const platformCards = computed(() => {
  const dockerTargets = installOptions.value.targets.filter((target) => target.deployment === 'docker')

  return [
    {
      id: 'linux',
      title: 'Linux',
      icon: 'fa-brands fa-linux',
      chips: ['systemd', 'amd64/arm64'],
      actions: [
        {
          id: 'linux-setup',
          label: 'Setup Script',
          type: 'setup',
          description: 'Install from Git',
          note: 'Installs a private Python runtime and systemd service under /opt/drastic-agent. Requires git, curl and root or sudo. Prompts for registration credentials.',
        },
        ...(showDevelopTab.value
          ? [
              {
                id: 'linux-develop',
                label: 'Develop',
                type: 'develop',
                description: 'Local development agent',
                note: 'Runs the agent from the current repository checkout using scripts/dev.sh agent-local. Configure credentials in .env.dev.override when needed.',
              },
            ]
          : []),
      ],
    },
    {
      id: 'docker',
      title: 'Docker',
      icon: 'fa-brands fa-docker',
      chips: ['Docker', 'Compose', 'amd64/arm64'],
      actions: [
        {
          id: 'docker-run',
          label: 'Docker',
          type: 'docker-run',
          image: dockerImage(dockerTargets),
          description: 'Docker run agent',
          note: 'Runs the agent container with persistent data and readonly host access.',
        },
        {
          id: 'docker-compose',
          label: 'Compose',
          type: 'docker-compose',
          image: dockerImage(dockerTargets),
          description: 'Docker Compose agent',
          note: 'Creates a standalone Compose file under /opt/drastic-agent and starts the agent service.',
        },
      ],
    },
  ]
})

function selectedActionId(card) {
  return selectedActions.value[card.id] || card.actions[0]?.id || ''
}

function setSelectedAction(card, actionId) {
  selectedActions.value = { ...selectedActions.value, [card.id]: actionId }
}

function buildActionSnippet(action) {
  if (action.type === 'setup') {
    return `curl -fsSL ${quoteShell(setupScriptUrl())} | bash`
  }

  if (action.type === 'docker-run') {
    return buildDockerRunSnippet(action.image)
  }

  if (action.type === 'docker-compose') {
    return buildDockerComposeSnippet(action.image)
  }

  if (action.type === 'develop') {
    return buildDevelopSnippet()
  }

  return ''
}

function buildDevelopSnippet() {
  return './scripts/dev.sh agent-local'
}

function buildDockerRunSnippet(image) {
  const serverUrl = publicBaseUrl.value

  return [
    'docker run -d',
    '--name drastic-agent',
    '--restart unless-stopped',
    '--add-host host.docker.internal:host-gateway',
    `-e DRASTIC_SERVER=${quoteShell(serverUrl)}`,
    "-e DRASTIC_USER='<your-username>'",
    "-e DRASTIC_PASSWORD='<your-password>'",
    '-e DRASTIC_LOGLEVEL=INFO',
    '-v /opt/drastic-agent/data:/app/data',
    '-v /:/mnt/host:ro',
    '-v /var/run/docker.sock:/var/run/docker.sock',
    image,
  ].join(' ')
}

function buildDockerComposeSnippet(image) {
  const serverUrl = publicBaseUrl.value

  return [
    'mkdir -p /opt/drastic-agent',
    "cat > /opt/drastic-agent/docker-compose.yaml <<'EOF'",
    'services:',
    '  agent:',
    `    image: ${image}`,
    '    restart: unless-stopped',
    '    extra_hosts:',
    '      - host.docker.internal:host-gateway',
    '    environment:',
    `      DRASTIC_SERVER: ${serverUrl}`,
    '      DRASTIC_USER: <your-username>',
    '      DRASTIC_PASSWORD: <your-password>',
    '      DRASTIC_LOGLEVEL: INFO',
    '    volumes:',
    '      - /opt/drastic-agent/data:/app/data',
    '      - /:/mnt/host:ro',
    '      - /var/run/docker.sock:/var/run/docker.sock',
    'EOF',
    '',
    'docker compose -f /opt/drastic-agent/docker-compose.yaml up -d',
  ].join('\n')
}

function dockerImage(dockerTargets) {
  return dockerTargets[0]?.image || 'drastic-backup-agent:latest'
}

function setupScriptUrl() {
  return `${publicBaseUrl.value}/install`
}

function quoteShell(value) {
  return `'${String(value || '').replaceAll("'", "'\\''")}'`
}

async function copyAction(action) {
  try {
    await copyToClipboard(buildActionSnippet(action))
    $q.notify({ message: `${action.label} snippet copied`, color: 'green', position: 'top' })
  } catch {
    $q.notify({ message: 'Could not copy snippet', color: 'red', position: 'top' })
  }
}

async function loadInstallOptions() {
  loading.value = true
  loadError.value = ''

  try {
    installOptions.value = await agentStore.getInstallOptions()
  } catch (error) {
    if (shouldIgnoreApiError(error)) return
    loadError.value = error.response?.data?.msg || 'Could not load install options. If you just changed backend routes, restart the backend container once.'
  } finally {
    loading.value = false
  }
}

onMounted(() => loadInstallOptions())

defineOptions({ name: 'AgentInstallPage' })
</script>
