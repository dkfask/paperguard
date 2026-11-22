<template>
  <div>
    <div v-if="uploadError" class="error-banner" role="alert">⚠ {{ uploadError }}</div>
    <div v-if="pasteError" class="error-banner" role="alert">⚠ {{ pasteError }}</div>
    <input ref="imgInput" type="file" accept=".png,.jpg,.jpeg,.gif,image/png,image/jpeg,image/gif" hidden @change="onImagePicked" />
    <div v-for="(par, i) in paragraphs" :key="par.id" class="para-block" :data-review-block-id="par.id">
      <div class="para-toolbar">
        <span class="para-no">{{ t('blockList.block', { number: i + 1 }) }}</span>

        <!-- 文本行内格式 -->
        <template v-if="!['image', 'table', 'equation'].includes(par.type)">
          <button class="tiny" :class="{ on: par.bold }" @click="toggle(par, 'bold')"><b>B</b></button>
          <button class="tiny" :class="{ on: par.italic }" @click="toggle(par, 'italic')"><i>I</i></button>
          <button class="tiny" :class="{ on: par.underline }" @click="toggle(par, 'underline')"><u>U</u></button>
          <template v-if="aiEnabled">
            <button class="tiny ai" :disabled="aiBusy" @click="emit('ai', { action: 'polish', index: i })">
              {{ aiBusy && aiBusy.index === i && aiBusy.action === 'polish' ? t('blockList.polishing') : t('blockList.polish') }}
            </button>
            <button class="tiny ai" :disabled="aiBusy" @click="emit('ai', { action: 'continue', index: i })">
              {{ aiBusy && aiBusy.index === i && aiBusy.action === 'continue' ? t('blockList.continuing') : t('blockList.continue') }}
            </button>
          </template>
        </template>
        <span v-else class="block-tag">{{ par.type === 'image' ? t('blockList.image') : par.type === 'equation' ? t('blockList.equation') : t('blockList.table') }}</span>

        <button class="tiny" @click="move(i, -1)" :disabled="i === 0">↑</button>
        <button class="tiny" @click="move(i, 1)" :disabled="i === paragraphs.length - 1">↓</button>
        <button class="tiny danger" @click="remove(i)">✖</button>
      </div>

      <!-- 文本段落 -->
      <textarea v-if="!['image', 'table', 'equation'].includes(par.type)"
                :value="par.text" rows="4" class="para-text" :class="styleClass"
                :style="styleVars"
                :placeholder="t('blockList.paragraphPlaceholder', { number: i + 1 })" @input="onParagraphInput(par, $event)" @paste="onTextPaste($event, i)" />

      <!-- 图片块 -->
      <div v-else-if="par.type === 'image'" class="image-block" tabindex="0" @paste="onImagePaste($event, par)">
        <p v-if="par.image?.smartart">{{ t('blockList.importedSmartArt') }}</p>
        <div v-else-if="par.image && par.image.url" class="image-preview">
          <img :src="imageSrc(par.image.url)" :alt="t('blockList.image')" />
          <button class="tiny" :disabled="uploading" @click="replaceImage(par)">🔄 {{ uploading ? t('blockList.uploading') : t('blockList.replaceImage') }}</button>
          <span class="hint-sm">{{ t('blockList.pasteToReplaceImage') }}</span>
        </div>
        <div v-else class="image-upload" :class="{ disabled: uploading }" @click="uploadFor(par)">
          <div class="icon">🖼</div>
          <div>{{ uploading ? t('blockList.imageUploading') : t('blockList.uploadImage') }}</div>
        </div>
        <div class="caption-row">
          <span class="caption-num">{{ t('blockList.figure', { chapter, number: figureIndex(i) }) }}</span>
          <input v-model="par.image.caption" class="caption-input"
                 :placeholder="t('blockList.captionPlaceholder', { title: sectionTitle })" @input="emitChange" />
        </div>
        <FigureTypographyGuide
          v-if="!par.image?.smartart"
          variant="inline"
          :caption-size-pt="figureCaptionSizePt"
          :manuscript-import-enabled="manuscriptImportEnabled"
          :strict-rule-active="imageTypographyRestricted"
        />
      </div>

      <!-- 表格块 -->
      <div v-else-if="par.type === 'table'" class="table-block">
        <div class="table-size">
          <label>{{ t('blockList.rows') }} <input type="number" min="1" max="50" v-model.number="par.table.rows" @change="resize(par)" /></label>
          <label>{{ t('blockList.columns') }} <input type="number" min="1" max="20" v-model.number="par.table.cols" @change="resize(par)" /></label>
          <span class="hint-sm">{{ t('blockList.resizeHint') }}</span>
        </div>
        <table class="cell-grid">
          <tr v-for="(row, r) in par.table.cells" :key="r">
            <td v-for="(cell, c) in row" :key="c">
              <input v-model="par.table.cells[r][c]" placeholder="" @input="emitChange"
                     @paste="onTablePaste($event, par, r, c)" />
            </td>
          </tr>
        </table>
        <div class="caption-row">
          <span class="caption-num">{{ t('blockList.tableNumber', { chapter, number: tableIndex(i) }) }}</span>
          <input v-model="par.table.caption" class="caption-input"
                 :placeholder="t('blockList.captionPlaceholder', { title: sectionTitle })" @input="emitChange" />
        </div>
      </div>

      <div v-else-if="par.type === 'equation'" class="equation-block">
        <span class="equation-mark">∑</span>
        <span>{{ par.equation?.alt_text || t('blockList.officeEquation') }}</span>
        <small>{{ t('blockList.officeEquationDetail') }}</small>
      </div>
    </div>

    <!-- 插入按钮 -->
    <div class="insert-row">
      <button class="primary" @click="addParagraph">➕ {{ t('blockList.addParagraph') }}</button>
      <button @click="addImage">🖼 {{ t('blockList.addImage') }}</button>
      <button @click="addTable">📊 {{ t('blockList.addTable') }}</button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onBeforeUnmount } from 'vue'
import { getPrivateImageUrl, uploadProjectImage } from '../api'
import { useI18n } from '../i18n'
import { newParagraphId } from '../utils/sectionDocument'
import { reconcileInlineRuns } from '../utils/inlineRuns'
import { appendClipboardRun, parseClipboardContent, sliceParagraphRuns } from '../utils/clipboardContent'
import FigureTypographyGuide from './writer/FigureTypographyGuide.vue'

const { t } = useI18n()

const props = defineProps({
  paragraphs: { type: Array, required: true },
  projectId: { type: String, required: true },
  styleClass: { type: String, default: '' },
  styleVars: { type: Object, default: () => ({}) },
  sectionTitle: { type: String, default: '' },
  chapter: { type: Number, default: 0 },   // 所属章号(题注自动编号用)
  figureOffset: { type: Number, default: 0 },
  tableOffset: { type: Number, default: 0 },
  figureCaptionSizePt: { type: Number, default: null },
  manuscriptImportEnabled: { type: Boolean, default: false },
  imageTypographyRestricted: { type: Boolean, default: false },
  aiEnabled: { type: Boolean, default: false },
  aiBusy: { type: Object, default: null }, // { index, action } 当前 AI 处理中的块
})
const emit = defineEmits(['change', 'ai'])

const imgInput = ref(null)
const imageUrls = reactive(new Map())
const uploadError = ref('')
const pasteError = ref('')
const uploading = ref(false)
let pendingTarget = null
let disposed = false

function imageSrc(path) {
  if (!path || !path.startsWith('/media/')) return path
  if (!imageUrls.has(path)) {
    imageUrls.set(path, '')
    getPrivateImageUrl(path).then((url) => imageUrls.set(path, url)).catch((err) => {
      uploadError.value = err.message
    })
  }
  return imageUrls.get(path) || ''
}

/** 该块在本章中的图序号(第几个图) */
function figureIndex(i) {
  let n = props.figureOffset
  for (let k = 0; k <= i; k++) {
    if (props.paragraphs[k]?.type === 'image') n += 1
  }
  return n
}

/** 该块在本章中的表序号 */
function tableIndex(i) {
  let n = props.tableOffset
  for (let k = 0; k <= i; k++) {
    if (props.paragraphs[k]?.type === 'table') n += 1
  }
  return n
}

function emitChange() { emit('change') }

function onParagraphInput(paragraph, event) {
  const nextText = event.target.value
  try {
    const runs = reconcileInlineRuns(paragraph.inline_runs, paragraph.text || '', nextText)
    if (paragraph.inline_runs?.length) paragraph.inline_runs = runs
    paragraph.text = nextText
    pasteError.value = ''
    emitChange()
  } catch {
    event.target.value = paragraph.text || ''
    pasteError.value = t('blockList.inlineFormatInvalid')
  }
}

function toggle(par, field) { par[field] = !par[field]; emit('change') }

function move(i, delta) {
  const j = i + delta
  if (j < 0 || j >= props.paragraphs.length) return
  const arr = props.paragraphs
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
  emit('change')
}

function remove(i) { props.paragraphs.splice(i, 1); emit('change') }

function addParagraph() {
  props.paragraphs.push({
    id: newParagraphId(), type: 'paragraph', text: '',
    bold: false, italic: false, underline: false,
  })
  emit('change')
}

function addImage() {
  props.paragraphs.push(imageBlock())
  emit('change')
}

function addTable() {
  props.paragraphs.push(tableBlock())
  emit('change')
}

function resize(par) {
  const t = par.table
  const rows = Math.max(1, Math.min(50, Number(t.rows) || 1))
  const cols = Math.max(1, Math.min(20, Number(t.cols) || 1))
  t.rows = rows; t.cols = cols
  // 调整网格, 保留已有内容
  t.cells = Array.from({ length: rows }, (_, r) =>
    Array.from({ length: cols }, (_, c) => t.cells[r]?.[c] ?? ''),
  )
  emit('change')
}

function uploadFor(par) {
  if (uploading.value) return
  pendingTarget = par
  imgInput.value?.click()
}

function replaceImage(par) {
  uploadFor(par)
}

async function uploadImage(file, target) {
  if (!file || !target) return false
  uploadError.value = ''
  uploading.value = true
  try {
    const { url } = await uploadProjectImage(props.projectId, file)
    target.image.url = url
    emit('change')
    return true
  } catch (err) {
    uploadError.value = t('blockList.imageUploadFailed', { message: err.message })
    return false
  } finally {
    uploading.value = false
  }
}

async function onImagePicked(e) {
  const f = e.target.files?.[0]
  e.target.value = ''
  const target = pendingTarget
  pendingTarget = null
  await uploadImage(f, target)
}

function imageBlock() {
  return {
    id: `img${Date.now()}-${Math.random().toString(16).slice(2)}`,
    type: 'image',
    image: { url: '', caption: '' },
  }
}

async function onTextPaste(e, index) {
  let content
  try {
    content = parseClipboardContent(e.clipboardData)
  } catch {
    e.preventDefault()
    pasteError.value = t('blockList.pasteUnsupported')
    return
  }
  if (content.native) return
  e.preventDefault()
  pasteError.value = ''
  if (uploading.value) {
    pasteError.value = t('blockList.pasteBusy')
    return
  }
  if (content.items.some(item => item.type === 'table' && !tableMatrixFits(item.matrix))) {
    pasteError.value = t('blockList.tablePasteLimit', { rows: 50, columns: 20 })
    return
  }
  const original = props.paragraphs[index]
  if (!original) return
  const snapshot = JSON.stringify(original)
  let blocks
  try {
    blocks = pastedBlocks(original, content.items, e.target.selectionStart, e.target.selectionEnd)
  } catch {
    pasteError.value = t('blockList.inlineFormatInvalid')
    return
  }
  const pending = blocks.filter(block => block.pendingFile)
  if (pending.length) {
    uploadError.value = ''
    uploading.value = true
    try {
      for (const block of pending) {
        const { url } = await uploadProjectImage(props.projectId, block.pendingFile)
        block.image.url = url
        delete block.pendingFile
      }
    } catch (err) {
      uploadError.value = t('blockList.imageUploadFailed', { message: err.message })
      return
    } finally {
      uploading.value = false
    }
  }
  if (disposed) return
  const currentIndex = props.paragraphs.indexOf(original)
  if (currentIndex === -1 || JSON.stringify(original) !== snapshot) {
    pasteError.value = t('blockList.pasteChanged')
    return
  }
  props.paragraphs.splice(currentIndex, 1, ...blocks)
  emitChange()
}

function onTablePaste(e, target, row, column) {
  let content
  try {
    content = parseClipboardContent(e.clipboardData)
  } catch {
    e.preventDefault()
    pasteError.value = t('blockList.pasteUnsupported')
    return
  }
  if (content.native || !target?.table) return
  const [item] = content.items
  if (content.items.length === 1 && item.type === 'paragraph'
      && item.runs.every(run => !run.bold && !run.italic && !run.underline && !run.highlight && !run.vert_align && !run.text.includes('\n'))) return
  e.preventDefault()
  pasteError.value = ''
  if (content.items.length !== 1 || item.type !== 'table') {
    pasteError.value = t('blockList.pasteUnsupported')
    return
  }
  const matrix = item.matrix
  if (!tableMatrixFits(matrix, row, column)) {
    pasteError.value = t('blockList.tablePasteLimit', { rows: 50, columns: 20 })
    return
  }
  writeTableMatrix(target.table, matrix, row, column)
  emitChange()
}

function pastedBlocks(original, items, start, end) {
  const text = String(original.text || '')
  const from = Math.max(0, Math.min(text.length, Number.isInteger(start) ? start : text.length))
  const to = Math.max(from, Math.min(text.length, Number.isInteger(end) ? end : from))
  const prefix = sliceParagraphRuns(original, 0, from)
  const suffix = sliceParagraphRuns(original, to, text.length)
  const pieces = items.map(item => {
    if (item.type === 'paragraph') return { type: 'paragraph', runs: item.runs.map(run => ({ ...run })) }
    if (item.type === 'table') return tableBlock(item.matrix)
    return { ...imageBlock(), pendingFile: item.file }
  })
  if (prefix.length) {
    if (pieces[0]?.type === 'paragraph') pieces[0].runs.unshift(...prefix)
    else pieces.unshift({ type: 'paragraph', runs: prefix })
  }
  if (suffix.length) {
    if (pieces.at(-1)?.type === 'paragraph') pieces.at(-1).runs.push(...suffix)
    else pieces.push({ type: 'paragraph', runs: suffix })
  }
  if (!pieces.some(piece => piece.type === 'paragraph')) pieces.unshift({ type: 'paragraph', runs: [] })
  let usedOriginal = false
  return pieces.map(piece => {
    if (piece.type !== 'paragraph') return piece
    const runs = []
    piece.runs.forEach(run => appendClipboardRun(runs, run))
    if (runs.length > 500) throw new Error('too-many-runs')
    const retained = !usedOriginal
    usedOriginal = true
    const hasFormat = runs.some(run => run.bold || run.italic || run.underline || run.highlight || run.vert_align)
    return { ...(retained ? original : {}), id: retained ? original.id : newParagraphId(), type: 'paragraph',
      text: runs.map(run => run.text).join(''), bold: false, italic: false, underline: false,
      highlight: null, vert_align: null, inline_runs: hasFormat ? runs : [] }
  })
}

function tableMatrixFits(matrix, startRow = 0, startColumn = 0) {
  return startRow + matrix.length <= 50
    && startColumn + Math.max(...matrix.map((line) => line.length), 1) <= 20
}

function tableBlock(matrix = null) {
  const rows = matrix ? Math.min(50, matrix.length) : 3
  const cols = matrix ? Math.min(20, Math.max(...matrix.map((line) => line.length), 1)) : 3
  return {
    id: `tbl${Date.now()}-${Math.random().toString(16).slice(2)}`,
    type: 'table',
    table: {
      rows,
      cols,
      cells: matrix
        ? matrix.slice(0, rows).map((line) => Array.from({ length: cols }, (_, index) => line[index] ?? ''))
        : Array.from({ length: rows }, () => Array(cols).fill('')),
      caption: '',
    },
  }
}

function writeTableMatrix(table, matrix, startRow = 0, startColumn = 0) {
  const maxRows = Math.max(table.rows || 0, startRow + matrix.length)
  const maxCols = Math.max(table.cols || 0, startColumn + Math.max(...matrix.map((line) => line.length), 1))
  table.rows = maxRows
  table.cols = maxCols
  table.cells = Array.from({ length: maxRows }, (_, row) =>
    Array.from({ length: maxCols }, (_, column) => table.cells?.[row]?.[column] ?? ''),
  )
  matrix.slice(0, maxRows - startRow).forEach((line, rowOffset) => {
    line.slice(0, maxCols - startColumn).forEach((value, columnOffset) => {
      table.cells[startRow + rowOffset][startColumn + columnOffset] = value
    })
  })
}

async function onImagePaste(e, target) {
  if (target?.image?.smartart) return
  let content
  try {
    content = parseClipboardContent(e.clipboardData)
  } catch {
    e.preventDefault()
    pasteError.value = t('blockList.pasteUnsupported')
    return
  }
  if (content.native || !target) return
  e.preventDefault()
  if (!content.items.length || content.items.some(item => item.type !== 'image')) {
    pasteError.value = t('blockList.pasteUnsupported')
    return
  }
  if (uploading.value) {
    pasteError.value = t('blockList.pasteBusy')
    return
  }
  const files = content.items.map(item => item.file)
  pasteError.value = ''
  uploadError.value = ''
  const snapshot = JSON.stringify(target)
  const urls = []
  uploading.value = true
  try {
    for (const file of files) {
      const { url } = await uploadProjectImage(props.projectId, file)
      urls.push(url)
    }
  } catch (err) {
    uploadError.value = t('blockList.imageUploadFailed', { message: err.message })
    return
  } finally {
    uploading.value = false
  }
  if (disposed) return
  const targetIndex = props.paragraphs.indexOf(target)
  if (targetIndex === -1 || JSON.stringify(target) !== snapshot) {
    pasteError.value = t('blockList.pasteChanged')
    return
  }
  target.image.url = urls[0]
  const blocks = urls.slice(1).map(url => ({ ...imageBlock(), image: { url, caption: '' } }))
  props.paragraphs.splice(targetIndex + 1, 0, ...blocks)
  emitChange()
}

onBeforeUnmount(() => {
  disposed = true
  for (const url of imageUrls.values()) {
    if (url) URL.revokeObjectURL(url)
  }
})
</script>

<style scoped>
.para-block { border: 1px solid var(--border); border-radius: 8px; margin-bottom: 12px; background: var(--card); }
.para-toolbar { display: flex; align-items: center; gap: 6px; padding: 6px 10px; border-bottom: 1px solid var(--border); }
.para-no { font-size: 12px; color: var(--muted); margin-right: auto; }
.block-tag { font-size: 12px; color: var(--primary); background: #eff6ff; padding: 2px 10px; border-radius: 999px; }
button.tiny { font-size: 12px; padding: 2px 8px; }
button.tiny.on { background: var(--primary); color: #fff; border-color: var(--primary); }
button.tiny.danger { color: var(--error); }
button.tiny.ai { color: var(--primary); }
button.tiny.ai:disabled { opacity: .55; }
.para-text { width: 100%; border: none; padding: 10px 12px; font-size: 14px; resize: vertical; line-height: 1.6; font-family: inherit; }
.para-text:focus { outline: none; }

.image-block { padding: 12px; }
.image-preview { text-align: center; }
.image-preview img { max-width: 100%; max-height: 320px; border: 1px solid var(--border); border-radius: 8px; }
.image-preview .tiny { margin-top: 8px; }
.image-upload {
  border: 2px dashed var(--border); border-radius: 8px; padding: 24px; text-align: center;
  cursor: pointer; color: var(--muted);
}
.image-upload:hover { border-color: var(--primary); color: var(--primary); }
.image-upload.disabled { opacity: .6; cursor: wait; pointer-events: none; }
.image-upload .icon { font-size: 28px; }
.caption-input { width: 100%; margin-top: 10px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; font-size: 13px; }
.caption-row { display: flex; align-items: center; gap: 10px; }
.caption-num { font-size: 13px; color: var(--primary); background: #eff6ff; padding: 4px 10px; border-radius: 6px; white-space: nowrap; margin-top: 10px; }

.table-block { padding: 12px; }
.table-size { display: flex; align-items: center; gap: 16px; margin-bottom: 10px; font-size: 13px; color: var(--muted); }
.table-size input { width: 60px; padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; }
.hint-sm { font-size: 12px; }
.cell-grid { width: 100%; border-collapse: collapse; }
.cell-grid td { border: 1px solid var(--border); padding: 2px; }
.cell-grid input { width: 100%; border: none; padding: 6px 8px; font-size: 13px; text-align: center; }
.cell-grid input:focus { outline: 1px solid var(--primary); }
.equation-block { display: flex; align-items: center; gap: 10px; margin: 12px; padding: 14px 16px; border-left: 3px solid var(--primary); background: #f8fafc; color: var(--on-surface); }
.equation-mark { font-size: 22px; color: var(--primary); }
.equation-block small { margin-left: auto; color: var(--muted); }

.insert-row { display: flex; gap: 10px; margin-top: 4px; flex-wrap: wrap; }
@media (max-width: 600px) {
  .para-toolbar { flex-wrap: wrap; }
  .para-no { flex-basis: 100%; }
  .table-size { flex-wrap: wrap; gap: 8px; }
  .cell-grid { min-width: 520px; }
  .table-block { overflow-x: auto; }
}
</style>
