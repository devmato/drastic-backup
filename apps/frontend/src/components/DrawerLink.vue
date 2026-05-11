<template>
  <q-item clickable :to="to" v-ripple>
    <q-item-section avatar>
      <q-icon :name="icon"></q-icon>
    </q-item-section>
    <q-item-section>
      <q-item-label class="text-weight-bold">{{ label }}</q-item-label>
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
