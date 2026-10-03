import { boot } from 'quasar/wrappers'
import { Dark } from 'quasar'

export default boot(() => {
  // Quasar's initial dark: 'auto' configuration resolves the system preference.
  let dark = Dark.isActive
  try {
    const saved = localStorage.getItem('drastic-theme')
    if (saved === 'light' || saved === 'dark') dark = saved === 'dark'
  } catch {
    // Browser storage can be unavailable; keep the system preference.
  }
  Dark.set(dark)
})
