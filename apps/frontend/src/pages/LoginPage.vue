
<template>
  <q-page class="flex flex-center q-pa-md">
    <q-card flat bordered class="db-auth-card">
      <q-card-section class="text-center q-pa-lg">
        <img
          src="/app/icons/drastic-backup-icon.svg"
          width="96"
          height="96"
          alt="dRastic Backup"
          class="block q-mx-auto q-mb-md"
        />
        <h1 class="text-h5 text-weight-medium q-my-none">dRastic Backup</h1>
        <div class="text-body2 q-mt-xs" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
          {{ needsInit ? 'Please create an admin account' : 'Please sign in' }}
        </div>
      </q-card-section>

      <q-banner
        v-if="needsInit"
        dense
        rounded
        class="bg-amber-1 text-amber-10 q-mx-md q-mb-sm"
      >
        No account exists yet. Please create the initial admin account to continue.
      </q-banner>

      <q-form @submit="onSubmit">
        <q-card-section class="q-px-lg q-py-none">
          <q-input
            outlined
            autofocus
            v-model="username"
            label="Username"
            autocomplete="username"
            lazy-rules
            :rules="[val => !!val || 'Required']"
          >
            <template v-slot:prepend><q-icon name="person" /></template>
          </q-input>

          <q-input
            outlined
            v-model="password"
            :type="showPassword ? 'password' : 'text'"
            label="Password"
            autocomplete="current-password"
            lazy-rules
            :rules="[val => !!val || 'Required']"
          >
            <template v-slot:prepend><q-icon name="lock" /></template>
            <template v-slot:append>
              <q-icon :name="showPassword ? 'visibility' : 'visibility_off'" @click="showPassword = !showPassword" class="cursor-pointer" />
            </template>
          </q-input>

          <q-input
            outlined
            v-if="needsInit"
            v-model="confirmPassword"
            :type="showPassword ? 'password' : 'text'"
            label="Confirm password"
            autocomplete="new-password"
            lazy-rules
            :rules="[
              val => !!val || 'Required',
              val => val === password || 'Passwords do not match'
            ]"
          >
            <template v-slot:prepend><q-icon name="verified_user" /></template>
          </q-input>
        </q-card-section>

        <q-card-actions class="q-pa-lg">
          <q-btn
            :label="needsInit ? 'Create admin account' : 'Login'"
            type="submit"
            unelevated
            no-caps
            class="full-width"
            color="primary"
            :loading="submitting"
          />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-page>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from 'stores/user'
import { useQuasar } from 'quasar'

const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const showPassword = ref(true)
const submitting = ref(false)

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const $q = useQuasar()

const needsInit = computed(() => userStore.needsInit)

async function loadNeedsInit() {
  try {
    await userStore.fetchNeedsInit()
  } catch {
    $q.notify({
      message: 'Unable to determine whether initial setup is required',
      position: 'top',
      color: 'red'
    })
  }
}

async function completeLogin() {
  await userStore.login(username.value.trim().toLowerCase(), password.value)
  await userStore.getUser()
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
  router.push(redirect)
}

async function createInitialUser() {
  if (password.value !== confirmPassword.value) {
    $q.notify({
      message: 'Passwords do not match',
      position: 'top',
      color: 'red'
    })
    return
  }

  await userStore.initializeUser(username.value.trim().toLowerCase(), password.value)
  await completeLogin()
  $q.notify({
    message: 'Admin account created successfully',
    position: 'top',
    color: 'green'
  })
}

async function onSubmit() {
  submitting.value = true
  try {
    if (needsInit.value) {
      await createInitialUser()
    } else {
      await completeLogin()
    }
  } catch (error) {
    if (needsInit.value && error.response?.status === 400) {
      await loadNeedsInit()
      $q.notify({
        message: 'The initial user was already created. Please log in now.',
        position: 'top',
        color: 'orange'
      })
      return
    }

    $q.notify({
      message: needsInit.value ? 'Unable to create the initial user' : 'Wrong username or password',
      position: 'top',
      color: 'red'
    })
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  loadNeedsInit()
})

defineOptions({ name: 'LoginPage' })
</script>
