<template>
  <div class="document-page">
    <section class="document-hero">
      <div>
        <p class="workspace-kicker">{{ t('documentProjects.kicker') }}</p>
        <h1>{{ t('documentProjects.title') }}</h1>
        <p>{{ t('documentProjects.description') }}</p>
      </div>
      <span class="material-symbols-outlined hero-icon" aria-hidden="true">article</span>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

    <section class="document-section">
      <div class="section-heading">
        <div>
          <p class="workspace-kicker">{{ t('documentProjects.packagesEyebrow') }}</p>
          <h2>{{ t('documentProjects.packagesTitle') }}</h2>
        </div>
        <button class="small" type="button" :disabled="loading" @click="load">
          <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
          {{ t('common.refresh') }}
        </button>
      </div>

      <div v-if="loading" class="empty-state">{{ t('common.loading') }}</div>
      <div v-else-if="!packages.length" class="empty-state">
        <span class="material-symbols-outlined" aria-hidden="true">inventory_2</span>
        <strong>{{ t('documentProjects.noPackages') }}</strong>
        <span>{{ t('documentProjects.noPackagesDetail') }}</span>
      </div>
      <div v-else class="package-grid">
        <article v-for="item in packages" :key="item.package.id" class="package-card">
          <div class="package-card-head">
            <span class="material-symbols-outlined" aria-hidden="true">description</span>
            <div>
              <h3>{{ item.template.name }}</h3>
              <p>{{ item.spec_set.name }}</p>
            </div>
          </div>
          <dl class="package-meta">
            <div><dt>{{ t('documentProjects.templateVersion') }}</dt><dd>{{ item.package.template_version_id }}</dd></div>
            <div><dt>{{ t('documentProjects.specVersion') }}</dt><dd>{{ item.package.spec_version_id }}</dd></div>
          </dl>
          <div class="create-row">
            <input v-model="titles[item.package.id]" :aria-label="t('documentProjects.projectTitle')" :placeholder="t('documentProjects.projectTitlePlaceholder')" maxlength="300" />
            <button class="primary small" type="button" :disabled="creating === item.package.id" @click="createProject(item.package.id)">
              <span class="material-symbols-outlined" aria-hidden="true">add</span>
              {{ creating === item.package.id ? t('common.creating') : t('documentProjects.start') }}
            </button>
          </div>
        </article>
      </div>
    </section>

    <section class="document-section">
      <div class="section-heading">
        <div>
          <p class="workspace-kicker">{{ t('documentProjects.projectsEyebrow') }}</p>
          <h2>{{ t('documentProjects.projectsTitle') }}</h2>
        </div>
      </div>
      <div v-if="!projects.length" class="empty-state compact">{{ t('documentProjects.noProjects') }}</div>
      <div v-else class="project-list">
        <RouterLink v-for="project in projects" :key="project.id" :to="`/document-projects/${project.id}`" class="project-row">
          <span class="material-symbols-outlined" aria-hidden="true">edit_document</span>
          <span class="project-row-main"><strong>{{ project.title || t('documentProjects.untitled') }}</strong><small>{{ project.document_type_id }} · {{ project.section_count }} {{ t('documentProjects.sections') }}</small></span>
          <span class="project-row-revision">v{{ project.revision }}</span>
          <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
        </RouterLink>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from '../i18n'
import { createDocumentProject, getDocumentPackages, getDocumentProjects } from '../api.js'

const router = useRouter()
const { t } = useI18n()
const packages = ref([])
const projects = ref([])
const titles = reactive({})
const loading = ref(false)
const creating = ref('')
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [packageList, projectList] = await Promise.all([getDocumentPackages(), getDocumentProjects()])
    packages.value = packageList
    projects.value = projectList
  } catch (exception) {
    error.value = exception.message
  } finally {
    loading.value = false
  }
}

async function createProject(packageId) {
  creating.value = packageId
  error.value = ''
  try {
    const project = await createDocumentProject({ package_id: packageId, title: titles[packageId] || '' })
    await router.push(`/document-projects/${project.id}`)
  } catch (exception) {
    error.value = exception.message
  } finally {
    creating.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.document-page { max-width: 1180px; margin: 0 auto; padding: 28px 20px 56px; }
.document-hero { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; padding: 24px 0 32px; border-bottom: 1px solid var(--border); }
.document-hero h1 { margin: 4px 0 8px; font-size: 25px; }
.document-hero p:not(.workspace-kicker) { max-width: 680px; margin: 0; color: var(--on-surface-variant); line-height: 1.7; }
.hero-icon { color: var(--primary); font-size: 48px; }
.document-section { padding: 28px 0; border-bottom: 1px solid var(--border); }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.section-heading h2 { margin: 3px 0 0; font-size: 22px; }
.package-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 14px; }
.package-card { padding: 18px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.package-card-head { display: flex; gap: 12px; align-items: flex-start; }
.package-card-head > .material-symbols-outlined { color: var(--primary); font-size: 28px; }
.package-card h3 { margin: 0 0 3px; font-size: 17px; }
.package-card p { margin: 0; color: var(--on-surface-variant); font-size: 13px; }
.package-meta { display: grid; gap: 8px; margin: 18px 0; }
.package-meta div { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; }
.package-meta dt { color: var(--on-surface-variant); }
.package-meta dd { margin: 0; text-align: right; word-break: break-word; }
.create-row { display: flex; gap: 8px; align-items: center; }
.create-row input { min-width: 0; flex: 1; padding: 9px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--on-surface); }
.empty-state { display: grid; justify-items: center; gap: 8px; padding: 38px 16px; color: var(--on-surface-variant); text-align: center; }
.empty-state .material-symbols-outlined { color: var(--primary); font-size: 36px; }
.empty-state.compact { padding: 24px 16px; }
.project-list { display: grid; gap: 8px; }
.project-row { display: flex; align-items: center; gap: 12px; padding: 14px 16px; border: 1px solid var(--border); border-radius: 8px; color: inherit; text-decoration: none; }
.project-row:hover { border-color: var(--primary); background: color-mix(in srgb, var(--primary) 5%, var(--surface)); }
.project-row > .material-symbols-outlined { color: var(--primary); }
.project-row-main { display: grid; gap: 3px; flex: 1; min-width: 0; }
.project-row-main strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.project-row-main small, .project-row-revision { color: var(--on-surface-variant); font-size: 12px; }
@media (max-width: 640px) { .document-page { padding: 18px 14px 40px; } .document-hero { padding-top: 10px; } .hero-icon { display: none; } .create-row { align-items: stretch; flex-direction: column; } .create-row input, .create-row button { width: 100%; } }
</style>