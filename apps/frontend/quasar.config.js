/* eslint-env node */

// https://v2.quasar.dev/quasar-cli-vite/quasar-config-js
const { configure } = require('quasar/wrappers');

const backendPort = Number(process.env.DRASTIC_BIND_BACKEND_PORT || 5050);
const frontendDevPort = Number(process.env.DRASTIC_BIND_DEV_FRONTEND_PORT || 9050);
const apiProxyTarget = process.env.QUASAR_API_PROXY_TARGET || `http://127.0.0.1:${backendPort}`;


module.exports = configure(function () {
  return {
    boot: ['axios'],
    css: ['app.scss'],
    extras: [
      'fontawesome-v6',
      'roboto-font',
      'material-icons',
    ],
    build: {
      target: {
        browser: [ 'es2019', 'edge88', 'firefox78', 'chrome87', 'safari13.1' ],
        node: 'node20'
      },

      vueRouterMode: 'history',
      publicPath: 'app',

      vitePlugins: [
        ['vite-plugin-checker', {
          eslint: {
            lintCommand: 'eslint "./**/*.{js,mjs,cjs,vue}"'
          }
        }, { server: false }]
      ]
    },

    devServer: {
      open: false,
      port: frontendDevPort,
      proxy: {
        '/api': {
          target: apiProxyTarget,
          changeOrigin: true,
          pathRewrite: { '^/api': '/api' }
        },
        '/install': {
          target: apiProxyTarget,
          changeOrigin: true,
          pathRewrite: { '^/install': '/install' }
        },
        '/docs': {
          target: apiProxyTarget,
          changeOrigin: true,
          pathRewrite: { '^/docs': '/docs' }
        },
        '/socket.io': {
          target: apiProxyTarget,
          changeOrigin: true,
          ws: true,
          pathRewrite: { '^/socket.io': '/socket.io' }
        }
      }
    },

    framework: {
      config: {
        notify: {}
      },

      lang: 'de-DE',
      plugins: [
        'Notify',
        'Loading',
        'Dialog'
      ]
    },

    animations: [],
  }
});
