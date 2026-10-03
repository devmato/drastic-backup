<template>
  <div class="fullscreen text-center q-pa-md flex flex-center">
    <div>
      <div class="text-h1 text-primary text-weight-medium">
        404
      </div>

      <h1 class="text-h5 q-mt-lg q-mb-sm">Page not found</h1>
      <p class="text-body2" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">The page you are looking for is not available.</p>

      <q-btn
        class="q-mt-md"
        color="primary"
        icon="home"
        unelevated
        to="/"
        label="Go Home"
        no-caps
      />
    </div>
  </div>
</template>

<script setup>
defineOptions({
  name: 'ErrorNotFound'
})
</script>
