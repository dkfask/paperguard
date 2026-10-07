<template>
  <component :is="workspaceComponent" />
</template>

<script setup>
import { computed } from 'vue'
import { auth } from '../auth'
import AdminWorkspace from './AdminWorkspace.vue'
import StudentWorkspace from './StudentWorkspace.vue'
import TeacherWorkspace from './TeacherWorkspace.vue'

const workspaceComponent = computed(() => auth.user?.role === 'student'
  ? StudentWorkspace
  : auth.user?.role === 'teacher'
    ? TeacherWorkspace
    : AdminWorkspace)
</script>

