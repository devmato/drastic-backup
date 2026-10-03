import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'

test('theme boot preselects the system mode unless light or dark was explicitly saved', () => {
  const source = readFileSync(new URL('../src/boot/theme.js', import.meta.url), 'utf8')
    .replace(/^import .*$/gm, '').replace('export default boot', 'boot')
  for (const systemDark of [false, true]) {
    for (const [saved, expected] of [[null, systemDark], ['auto', systemDark], ['light', false], ['dark', true], ['invalid', systemDark]]) {
      let mode
      runInNewContext(source, { boot: fn => fn(), Dark: { isActive: systemDark, set: value => { mode = value } }, localStorage: { getItem: () => saved } })
      assert.equal(mode, expected)
    }
    let mode
    runInNewContext(source, { boot: fn => fn(), Dark: { isActive: systemDark, set: value => { mode = value } }, localStorage: { getItem: () => { throw new Error('Storage unavailable') } } })
    assert.equal(mode, systemDark)
  }
})
