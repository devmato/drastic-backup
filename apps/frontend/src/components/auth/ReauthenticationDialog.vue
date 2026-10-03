<template>
  <q-dialog v-model="dialogVisible" persistent>
    <q-card class="db-dialog-card-sm">
      <q-card-section>
        <div class="text-h6">Confirm Password</div>
        <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
          For security-sensitive repository operations, please enter your account password again.
        </div>
      </q-card-section>

      <q-form @submit="submitForm">
        <q-card-section class="q-gutter-sm">
          <q-input
            ref="passwordInput"
            outlined
            v-model="password"
            :type="showPassword ? 'text' : 'password'"
            label="Account Password"
            autocomplete="current-password"
            autofocus
            :rules="[val => !!val || 'Required']"
            :disable="userStore.reauthSubmitting"
          >
            <template #prepend><q-icon name="lock" /></template>
            <template #append>
              <q-icon :name="showPassword ? 'visibility_off' : 'visibility'" class="cursor-pointer" @click="showPassword = !showPassword" />
            </template>
          </q-input>

          <q-banner v-if="errorMessage" dense rounded class="bg-red-1 text-red-10">
            {{ errorMessage }}
          </q-banner>
        </q-card-section>

        <q-card-actions align="right" class="q-pa-md">
          <q-btn flat no-caps label="Cancel" :disable="userStore.reauthSubmitting" @click="userStore.cancelReauth()" />
          <q-btn unelevated no-caps label="Continue" type="submit" color="primary" :loading="userStore.reauthSubmitting" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useUserStore } from 'stores/user'
import { getApiErrorMessage } from 'src/utils/api-error'

const userStore = useUserStore()
const password = ref('')
const showPassword = ref(false)
const errorMessage = ref('')
const passwordInput = ref(null)

const dialogVisible = computed({
  get: () => userStore.reauthDialogOpen,
  set: value => {
    if (!value && userStore.reauthDialogOpen) {
      userStore.cancelReauth()
    }
  },
})

async function submitForm() {
  errorMessage.value = ''
  try {
    await userStore.confirmReauth(password.value)
  } catch (error) {
    errorMessage.value = error?.response?.status === 401
      ? 'Wrong account password'
      : getApiErrorMessage(error, error.message || 'Password confirmation failed')
  }
}

watch(() => userStore.reauthDialogOpen, async value => {
  if (!value) {
    password.value = ''
    errorMessage.value = ''
    showPassword.value = false
    return
  }

  await nextTick()
  passwordInput.value?.focus?.()
})

defineOptions({ name: 'ReauthenticationDialog' })
</script>
