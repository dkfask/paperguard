<template>
  <div class="node">
    <div class="node-row">
      <select v-model="node.type" class="type-select">
        <option value="cover">{{ t('section.cover') }}</option>
        <option value="abstract">{{ t('section.abstract') }}</option>
        <option value="english_abstract">{{ t('section.englishAbstract') }}</option>
        <option value="keywords">{{ t('section.keywords') }}</option>
        <option value="english_keywords">{{ t('section.englishKeywords') }}</option>
        <option value="toc">{{ t('section.toc') }}</option>
        <option value="heading1">{{ t('section.heading1') }}</option>
        <option value="heading2">{{ t('section.heading2') }}</option>
        <option value="heading3">{{ t('section.heading3') }}</option>
        <option value="body">{{ t('section.body') }}</option>
        <option value="references">{{ t('section.references') }}</option>
        <option value="acknowledgement">{{ t('section.acknowledgement') }}</option>
        <option value="appendix">{{ t('section.appendix') }}</option>
      </select>
      <input v-model="node.title" :placeholder="t('section.titlePlaceholder')" class="title-input" />
      <label class="chk"><input type="checkbox" v-model="node.required" /> {{ t('editor.required') }}</label>
      <button type="button" :title="t('section.addChild')" :aria-label="t('section.addChild')" @click="addChild"><span class="material-symbols-outlined" aria-hidden="true">add</span></button>
      <button type="button" style="color: var(--error)" :title="t('section.remove')" :aria-label="t('section.remove')" @click="$emit('remove')"><span class="material-symbols-outlined" aria-hidden="true">delete</span></button>
    </div>
    <div v-if="node.children && node.children.length" class="children">
      <section-node v-for="child in node.children" :key="child.key" :node="child"
                    @remove="removeChild(child.key)" />
    </div>
  </div>
</template>

<script setup>
import { useI18n } from '../i18n'

defineOptions({ name: 'SectionNode' })
const { t } = useI18n()
const props = defineProps({
  node: { type: Object, required: true },
})
defineEmits(['remove'])

const TYPE_KEYS = {
  cover: 'cover', abstract: 'abstract', english_abstract: 'english_abstract', keywords: 'keywords', english_keywords: 'english_keywords', toc: 'toc', heading1: 'heading1',
  heading2: 'heading2', heading3: 'heading3', body: 'body',
  references: 'references', acknowledgement: 'acknowledgement', appendix: 'appendix',
}

function removeChild(key) {
  const index = props.node.children.findIndex((child) => child.key === key)
  if (index >= 0) props.node.children.splice(index, 1)
}

function addChild() {
  const parent = props.node
  const type = parent.type === 'heading1' ? 'heading2'
    : parent.type === 'heading2' ? 'heading3' : 'body'
  if (!parent.children) parent.children = []
  const used = new Set()
  function collect(nodes) {
    for (const item of nodes || []) {
      if (item.key) used.add(item.key)
      collect(item.children)
    }
  }
  collect(parent.children)
  let n = parent.children.length + 1
  let key = `${parent.key || 'sec'}_${n}`
  while (used.has(key) || key === parent.key) key = `${parent.key || 'sec'}_${++n}`
  parent.children.push({
    key,
    type: TYPE_KEYS[type] || 'body',
    title: type === 'body' ? t('section.newBody') : t('section.newChild'),
    required: true,
    children: [],
  })
}

function genKey(prefix, n) {
  // 去掉父级可能带的下划线层级, 生成层级 key: ch1 -> ch1_1 -> ch1_1_1
  const p = String(prefix).replace(/_\d+$/, '')
  return `${p}_${n}`
}
</script>

<style scoped>
.node { margin: 4px 0; }
.node-row { display: flex; align-items: center; gap: 8px; }
.type-select { padding: 6px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.title-input { padding: 6px; border: 1px solid var(--border); border-radius: 6px; flex: 1; font-size: 14px; }
.chk { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 4px; }
.children { margin-left: 32px; padding-left: 12px; border-left: 2px solid var(--border); }
@media (max-width: 600px) {
  .node-row { align-items: stretch; flex-wrap: wrap; }
  .type-select { flex: 1 1 130px; min-width: 0; }
  .title-input { flex: 1 1 180px; min-width: 0; }
  .chk { flex: 0 0 auto; }
  .children { margin-left: 12px; padding-left: 8px; }
}
</style>
