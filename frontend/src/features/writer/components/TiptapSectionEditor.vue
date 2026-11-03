<template>
  <div class="tiptap-editor" :style="styleVars">
    <div class="tiptap-toolbar" role="toolbar" :aria-label="t('tiptap.toolbar')">
      <button type="button" :title="t('tiptap.bold')" :aria-label="t('tiptap.bold')" :class="{ active: editor?.isActive('bold') }" @click="editor?.chain().focus().toggleBold().run()"><b>B</b></button>
      <button type="button" :title="t('tiptap.italic')" :aria-label="t('tiptap.italic')" :class="{ active: editor?.isActive('italic') }" @click="editor?.chain().focus().toggleItalic().run()"><i>I</i></button>
      <button type="button" :title="t('tiptap.underline')" :aria-label="t('tiptap.underline')" :class="{ active: editor?.isActive('underline') }" @click="editor?.chain().focus().toggleUnderline().run()"><u>U</u></button>
      <button type="button" :title="t('tiptap.superscript')" :aria-label="t('tiptap.superscript')" :class="{ active: editor?.isActive('pgSuperscript') }" @click="editor?.chain().focus().toggleMark('pgSuperscript').run()">x<sup>2</sup></button>
      <button type="button" :title="t('tiptap.subscript')" :aria-label="t('tiptap.subscript')" :class="{ active: editor?.isActive('pgSubscript') }" @click="editor?.chain().focus().toggleMark('pgSubscript').run()">x<sub>2</sub></button>
      <button type="button" :title="t('tiptap.clear')" :aria-label="t('tiptap.clear')" @click="editor?.chain().focus().unsetAllMarks().run()">{{ t('tiptap.clear') }}</button>
    </div>
    <div v-if="pasteError" class="tiptap-error" role="alert">{{ pasteError }}</div>
    <EditorContent :editor="editor" class="tiptap-content" />
  </div>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { Extension, Mark, Node } from '@tiptap/core'
import { Fragment, Slice } from '@tiptap/pm/model'
import { EditorContent, useEditor } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import { useI18n } from '../../../i18n'
import { blocksToSectionDocument, sectionDocumentText } from '../../../utils/sectionDocument'
import { parseClipboardContent } from '../../../utils/clipboardContent'

const { t } = useI18n()

const props = defineProps({
  modelValue: { type: String, default: '' },
  document: { type: Object, required: true },
  styleVars: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue', 'update:document'])
const pasteError = ref('')

const ParagraphIdentity = Extension.create({
  name: 'paragraphIdentity',
  addGlobalAttributes() {
    return [{ types: ['paragraph'], attributes: {
      blockId: { default: null, keepOnSplit: false, parseHTML: () => null,
        renderHTML: attrs => attrs.blockId ? { 'data-review-block-id': attrs.blockId } : {} },
    } }]
  },
})

const PaperMedia = Node.create({
  name: 'paperMedia', group: 'block', atom: true, selectable: true, draggable: true,
  addAttributes() { return { blockId: { default: null }, kind: { default: '' }, title: { default: '' } } },
  parseHTML() { return [] },
  renderHTML({ node }) {
    const label = t(`tiptap.media.${node.attrs.kind}`)
    const text = node.attrs.title ? `${label}：${node.attrs.title}` : label
    return ['div', { class: 'paper-media', contenteditable: 'false', role: 'note', 'aria-label': text,
      'data-review-block-id': node.attrs.blockId || null }, text]
  },
})

function verticalMark(name, tag, excludes) {
  return Mark.create({ name, excludes, parseHTML: () => [{ tag }], renderHTML: () => [tag, 0] })
}

const Highlight = Mark.create({
  name: 'pgHighlight',
  addAttributes() { return { color: { default: null } } },
  parseHTML() { return [{ tag: 'mark', getAttrs: element => ({ color: element.getAttribute('data-pg-highlight') || 'yellow' }) }] },
  renderHTML({ mark }) {
    const color = String(mark.attrs.color || '')
    const style = /^(?:[a-z]{1,32}|#[0-9a-f]{3,8})$/i.test(color) ? `background-color:${color}` : ''
    return ['mark', { 'data-pg-highlight': color, style }, 0]
  },
})

function handlePaste(view, event) {
  let content
  try {
    content = parseClipboardContent(event.clipboardData)
  } catch {
    pasteError.value = t('tiptap.unsupportedPaste')
    return true
  }
  pasteError.value = ''
  if (content.native) return false
  if (content.items.some(item => item.type !== 'paragraph' || item.runs.length > 500)) {
    pasteError.value = t('tiptap.unsupportedPaste')
    return true
  }
  const paragraphs = content.items.map(item => ({ type: 'paragraph', text: item.runs.map(run => run.text).join(''), inline_runs: item.runs }))
  const nodes = blocksToSectionDocument(paragraphs).content.map(node => view.state.schema.nodeFromJSON(node))
  view.dispatch(view.state.tr.replaceSelection(new Slice(Fragment.fromArray(nodes), 1, 1)).scrollIntoView())
  return true
}

const editor = useEditor({
  extensions: [
    StarterKit.configure({ blockquote: false, bulletList: false, orderedList: false, listItem: false,
      listKeymap: false, heading: false, code: false, codeBlock: false, horizontalRule: false,
      link: false, strike: false, trailingNode: false }),
    ParagraphIdentity, PaperMedia, Highlight,
    verticalMark('pgSuperscript', 'sup', 'pgSubscript'),
    verticalMark('pgSubscript', 'sub', 'pgSuperscript'),
  ],
  content: props.document,
  editorProps: {
    attributes: {
      class: 'tiptap-prose',
      spellcheck: 'true',
    },
    handlePaste,
  },
  onUpdate: ({ editor: currentEditor }) => {
    const document = currentEditor.getJSON()
    emit('update:modelValue', sectionDocumentText(document))
    emit('update:document', document)
  },
})

watch(() => props.document, (value) => {
  if (!editor.value) return
  if (JSON.stringify(editor.value.getJSON()) !== JSON.stringify(value)) {
    editor.value.commands.setContent(value, { emitUpdate: false, errorOnInvalidContent: true })
  }
}, { deep: true })

onBeforeUnmount(() => editor.value?.destroy())
</script>

<style scoped>
.tiptap-editor { overflow: hidden; border: 1px solid var(--border); border-radius: 8px; background: var(--card); }
.tiptap-toolbar { display: flex; flex-wrap: wrap; gap: 5px; padding: 8px; border-bottom: 1px solid var(--border); background: var(--surface-container-low); }
.tiptap-toolbar button { min-width: 34px; padding: 4px 8px; font-size: 12px; }
.tiptap-toolbar button.active { border-color: var(--primary); background: var(--primary-fixed); color: var(--on-primary-fixed); }
.tiptap-error { padding: 8px 12px; color: var(--danger, #b42318); }
.tiptap-content { min-height: 180px; padding: 12px; }
.tiptap-content :deep(.ProseMirror) { min-height: 155px; outline: none; white-space: pre-wrap; }
.tiptap-content :deep(p) { margin: 0 0 12px; }
.tiptap-content :deep(p:last-child) { margin-bottom: 0; }
.tiptap-content :deep(.paper-media) { margin: 8px 0; padding: 8px; border: 1px solid var(--border); background: var(--surface-container-low); color: var(--muted); }
.tiptap-content :deep(.paper-media.ProseMirror-selectednode) { outline: 2px solid var(--primary); }
</style>
