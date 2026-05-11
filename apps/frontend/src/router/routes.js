
const routes = [
  {
    path: '/',
    component: () => import('layouts/MainLayout.vue'),
    children: [
      { path: '', component: () => import('pages/IndexPage.vue') },
    ],
    meta: { auth: true }
  },

  {
    path: '/agents',
    component: () => import('layouts/MainLayout.vue'),
    children: [
      { path: '', component: () => import('pages/AgentsPage.vue') },
      { path: 'install', component: () => import('pages/AgentInstallPage.vue') },
      { path: ':agentId/operations', component: () => import('pages/AgentReportsPage.vue') },
      { path: ':agentId/operations/:operationId', component: () => import('pages/AgentOperationDetailPage.vue') },
    ],
    meta: { auth: true, title: 'Agents' }
  },

  {
    path: '/repositories',
    component: () => import('layouts/MainLayout.vue'),
    children: [
      { path: '', component: () => import('pages/RepositoriesPage.vue') },
    ],
    meta: { auth: true, title: 'Repositories' }
  },

  {
    path: '/jobs',
    component: () => import('layouts/MainLayout.vue'),
    children: [
      { path: '', component: () => import('pages/JobsPage.vue') },
    ],
    meta: { auth: true, title: 'Jobs' }
  },

  {
    path: '/retentions',
    component: () => import('layouts/MainLayout.vue'),
    children: [
      { path: '', component: () => import('pages/RetentionsPage.vue') },
    ],
    meta: { auth: true, title: 'Retention Policies' }
  },

  {
    path: '/notifications',
    component: () => import('layouts/MainLayout.vue'),
    children: [
      { path: '', component: () => import('pages/NotificationsPage.vue') },
    ],
    meta: { auth: true, title: 'Notifications' }
  },

  {
    path: '/login',
    component: () => import('layouts/LoginLayout.vue'),
    children: [
      { path: '', component: () => import('pages/LoginPage.vue') }
    ],
    meta: { auth: false, title: 'Login' }
  },

  {
    path: '/:catchAll(.*)*',
    component: () => import('pages/ErrorNotFound.vue')
  }
]

export default routes
