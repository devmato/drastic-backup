import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { ref } from 'vue'

const source = readFileSync(new URL('../src/pages/SettingsPage.vue', import.meta.url), 'utf8')
  .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')

test('account time zone can be searched and saved; failed writes preserve the selection', async () => {
  const notices = [], saved = []
  const userStore = { user: { schedule_timezone: 'UTC' }, updateSettings: async zone => {
    saved.push(zone)
    userStore.user = { schedule_timezone: zone }
  } }
  const page = runInNewContext(`${source}
    ;({ scheduleTimezone, filteredTimezones, timezoneError, timezoneBusy, loadTimezones, filterTimezones, saveTimezone })`, {
    ref, useRouter: () => ({}), useQuasar: () => ({ notify: value => notices.push(value) }),
    useUserStore: () => userStore, onMounted: () => {}, onBeforeUnmount: () => {},
    getTimezones: async () => ['UTC', 'Europe/Berlin', 'America/New_York'],
    getApiErrorMessage: error => error.message,
  })
  assert.equal(page.scheduleTimezone.value, 'UTC')
  await page.loadTimezones()
  page.filterTimezones('BERL', callback => callback())
  assert.deepEqual([...page.filteredTimezones.value], ['Europe/Berlin'])
  page.scheduleTimezone.value = 'Europe/Berlin'
  await page.saveTimezone()
  assert.deepEqual(saved, ['Europe/Berlin'])
  assert.equal(userStore.user.schedule_timezone, 'Europe/Berlin')
  assert.equal(notices.at(-1).color, 'positive')
  page.scheduleTimezone.value = 'America/New_York'
  userStore.updateSettings = async () => { throw new Error('Offline') }
  await page.saveTimezone()
  assert.equal(page.timezoneError.value, 'Offline')
  assert.equal(page.scheduleTimezone.value, 'America/New_York')
  assert.equal(userStore.user.schedule_timezone, 'Europe/Berlin')
  assert.equal(page.timezoneBusy.value, false)
})
