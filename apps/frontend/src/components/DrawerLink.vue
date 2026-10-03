<template>
  <q-item dark clickable :dense="!$q.platform.has.touch" :to="to" :exact="to === '/'" v-ripple active-class="db-nav-active text-blue-3" class="rounded-borders q-mx-sm q-my-xs">
    <q-icon :name="icon" size="sm" class="self-center q-mr-md" />
    <q-item-section>
      <q-item-label class="text-weight-medium">{{ label }}</q-item-label>
    </q-item-section>
  </q-item>
</template>

<script setup>
defineOptions({
  name: 'DrawerLink'
})

const props = defineProps({

  label: {
    type: String,
    default: ''
  },

  to: {
    type: String,
    default: '#'
  },

  icon: {
    type: String,
    default: ''
  }
})
</script>
