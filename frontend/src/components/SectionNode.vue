<template>
  <div class="node">
    <div class="node-row">
      <select v-model="node.type" class="type-select">
        <option value="cover">封面</option>
        <option value="abstract">摘要</option>
        <option value="toc">目录</option>
        <option value="heading1">一级标题(章)</option>
        <option value="heading2">二级标题(节)</option>
        <option value="heading3">三级标题(小节)</option>
        <option value="body">正文容器</option>
        <option value="references">参考文献</option>
        <option value="acknowledgement">致谢</option>
        <option value="appendix">附录</option>
      </select>
      <input v-model="node.title" placeholder="章节标题(如 第一章 绪论)" class="title-input" />
      <label class="chk"><input type="checkbox" v-model="node.required" /> 必填</label>
      <button @click="addChild">➕</button>
      <button style="color: var(--error)" @click="$emit('remove')">✖</button>
    </div>
    <div v-if="node.children && node.children.length" class="children">
      <section-node v-for="child in node.children" :key="child.key" :node="child"
                    @remove="removeChild(child.key)" />
    </div>
  </div>
</template>

<script setup>
defineOptions({ name: 'SectionNode' })
const props = defineProps({
  node: { type: Object, required: true },
})
defineEmits(['remove'])

const TYPE_KEYS = {
  cover: 'cover', abstract: 'abstract', toc: 'toc', heading1: 'heading1',
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
    title: type === 'body' ? '正文' : '小节',
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
