<template>
  <div class="tiptap-editor" :style="styleVars">
    <div class="tiptap-toolbar" role="toolbar" :aria-label="t('tiptap.toolbar')">
      <button type="button" :title="t('tiptap.bold')" :aria-label="t('tiptap.bold')" :class="{ active: editor?.isActive('bold') }" @click="editor?.chain().focus().toggleBold().run()"><b>B</b></button>
      <button type="button" :title="t('tiptap.italic')" :aria-label="t('tiptap.italic')" :class="{ active: editor?.isActive('italic') }" @click="editor?.chain().focus().toggleItalic().run()"><i>I</i></button>
      <button type="button" :title="t('tiptap.bulletList')" :aria-label="t('tiptap.bulletList')" :class="{ active: editor?.isActive('bulletList') }" @click="editor?.chain().focus().toggleBulletList().run()">• {{ t('tiptap.bulletList') }}</button>
      <button type="button" :title="t('tiptap.orderedList')" :aria-label="t('tiptap.orderedList')" :class="{ active: editor?.isActive('orderedList') }" @click="editor?.chain().focus().toggleOrderedList().run()">1. {{ t('tiptap.orderedList') }}</button>
      <button type="button" :title="t('tiptap.clear')" :aria-label="t('tiptap.clear')" @click="editor?.chain().focus().clearNodes().unsetAllMarks().run()">{{ t('tiptap.clear') }}</button>
    </div>
    <EditorContent :editor="editor" class="tiptap-content" />
    <div class="tiptap-help">{{ t('tiptap.help') }}</div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, watch } from 'vue'
import { EditorContent, useEditor } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import { useI18n } from '../../../i18n'

const { t } = useI18n()

const props = defineProps({
  modelValue: { type: String, default: '' },
  styleVars: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])

function escapeHtml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function toContent(value) {
  return value
    .split(/\n{2,}/)
    .map((paragraph) => `<p>${escapeHtml(paragraph).replaceAll('\n', '<br>')}</p>`)
    .join('') || '<p></p>'
}

const editor = useEditor({
  extensions: [StarterKit],
  content: toContent(props.modelValue),
  editorProps: {
    attributes: {
      class: 'tiptap-prose',
      spellcheck: 'true',
    },
  },
  onUpdate: ({ editor: currentEditor }) => {
    emit('update:modelValue', currentEditor.getText({ blockSeparator: '\n\n' }))
  },
})

watch(() => props.modelValue, (value) => {
  if (!editor.value) return
  const currentText = editor.value.getText({ blockSeparator: '\n\n' })
  if (currentText !== value) editor.value.commands.setContent(toContent(value), false)
})

onBeforeUnmount(() => editor.value?.destroy())
</script>

<style scoped>
.tiptap-editor { overflow: hidden; border: 1px solid var(--border); border-radius: 8px; background: var(--card); }
.tiptap-toolbar { display: flex; flex-wrap: wrap; gap: 5px; padding: 8px; border-bottom: 1px solid var(--border); background: var(--surface-container-low); }
.tiptap-toolbar button { min-width: 34px; padding: 4px 8px; font-size: 12px; }
.tiptap-toolbar button.active { border-color: var(--primary); background: var(--primary-fixed); color: var(--on-primary-fixed); }
.tiptap-content { min-height: 180px; padding: 12px; }
.tiptap-content :deep(.ProseMirror) { min-height: 155px; outline: none; white-space: pre-wrap; }
.tiptap-content :deep(p) { margin: 0 0 12px; }
.tiptap-content :deep(p:last-child) { margin-bottom: 0; }
.tiptap-content :deep(ul), .tiptap-content :deep(ol) { padding-left: 24px; margin: 8px 0; }
.tiptap-help { padding: 7px 12px; border-top: 1px solid var(--border); color: var(--muted); font-size: 11px; }
</style>
