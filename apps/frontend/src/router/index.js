import { route } from 'quasar/wrappers'
import { createRouter, createMemoryHistory, createWebHistory, createWebHashHistory } from 'vue-router'
import routes from './routes'
import { useUserStore } from 'stores/user'

export default route(function () {
  const createHistory = process.env.SERVER
    ? createMemoryHistory
    : (process.env.VUE_ROUTER_MODE === 'history' ? createWebHistory : createWebHashHistory)

  const Router = createRouter({
    scrollBehavior: () => ({ left: 0, top: 0 }),
    routes,
    history: createHistory(process.env.VUE_ROUTER_BASE)
  })

  const userStore = useUserStore()

  Router.beforeEach(async (to) => {
    if (!userStore.loggedIn && to.path !== '/login') {
      await userStore.getUser()
    }

    if (to.meta && to.meta.title) {
      document.title = `${to.meta.title} - dRastic Backup`
    } else {
      document.title = 'dRastic Backup'
    }

    if (to.meta.auth && !userStore.loggedIn) {
      if (to.fullPath !== '/') {
        return encodeURI('/login?redirect=' + to.fullPath)
      }
      return '/login'
    } else if (userStore.loggedIn && to.path === '/login') {
      return '/'
    }
  })

  return Router
})
