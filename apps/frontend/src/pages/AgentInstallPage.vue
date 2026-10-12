<template>
  <q-page class="q-pa-md">
    <PageHeader title="Add Agent" description="Install the Linux or macOS agent directly from Git or run the Docker agent. Copy the install snippet for the selected target.">
      <template #breadcrumbs>
        <q-breadcrumbs>
          <q-breadcrumbs-el label="Agents" icon="desktop_windows" to="/agents" />
          <q-breadcrumbs-el label="Add Agent" />
        </q-breadcrumbs>
      </template>
    </PageHeader>

    <q-banner v-if="loadError" rounded class="bg-red-1 text-red-10 q-mb-md">
      {{ loadError }}
      <template #action>
        <q-btn flat no-caps color="negative" label="Retry" @click="loadInstallOptions" />
      </template>
    </q-banner>

    <div v-if="loading" class="row justify-center q-py-xl">
      <q-spinner color="primary" size="40px" />
    </div>

    <div v-else class="row items-stretch q-col-gutter-md">
      <div v-for="card in platformCards" :key="card.id" class="col-12 col-md-6 col-lg-4 column">
        <AgentInstallCard :card="card" @copy="copyAction" />
      </div>
    </div>
  </q-page>
</template>

<script setup>
import PageHeader from 'components/PageHeader.vue'
import AgentInstallCard from 'components/agents/AgentInstallCard.vue'
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
const showDevelopTab = computed(() => ['dev', 'test'].includes(userStore.environment))
const publicBaseUrl = computed(() => (installOptions.value.server_url || window.location.origin).replace(/\/$/, ''))

const platformCards = computed(() => {
  const dockerTargets = installOptions.value.targets.filter((target) => target.deployment === 'docker')

  return [
    {
      id: 'linux',
      title: 'Linux',
      icon: 'fa-brands fa-linux',
      chips: ['systemd', 'amd64 / arm64'],
      actions: [
        {
          id: 'linux-setup',
          label: 'Setup Script',
          type: 'setup',
          description: 'Install from Git',
          note: 'Requires Git, curl and sudo. Installs a private Python runtime and systemd service.',
        },
        ...(showDevelopTab.value
          ? [
              {
                id: 'linux-develop',
                label: 'Develop',
                type: 'develop',
                description: 'Local development agent',
                note: 'Runs from a local checkout. Configure registration credentials in .env.dev.override.',
              },
            ]
          : []),
      ],
    },
    {
      id: 'macos',
      title: 'macOS',
      icon: 'fa-brands fa-apple',
      chips: ['launchd', 'Intel / Apple Silicon'],
      actions: [
        {
          id: 'macos-setup',
          label: 'Setup Script',
          type: 'setup',
          description: 'Install from Git',
          note: 'Requires Git (Apple Command Line Tools) and sudo. After setup, grant Full Disk Access as instructed.',
        },
      ],
    },
    {
      id: 'docker',
      title: 'Docker',
      icon: 'fa-brands fa-docker',
      chips: ['Container', 'amd64 / arm64'],
      actions: [
        {
          id: 'docker-run',
          label: 'Docker',
          type: 'docker-run',
          image: dockerImage(dockerTargets),
          description: 'Docker run agent',
          note: 'Requires Docker. Runs with persistent agent data and read-only access to host files.',
        },
        {
          id: 'docker-compose',
          label: 'Compose',
          type: 'docker-compose',
          image: dockerImage(dockerTargets),
          description: 'Docker Compose agent',
          note: 'Requires Docker Compose. Creates the agent service with persistent data and host access.',
        },
      ],
    },
  ].map(card => ({
    ...card,
    actions: card.actions.map(action => ({ ...action, snippet: buildActionSnippet(action) })),
  }))
})

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
    await copyToClipboard(action.snippet)
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
