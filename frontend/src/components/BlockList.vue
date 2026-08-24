<template>
  <div>
    <div v-if="uploadError" class="error-banner" role="alert">⚠ {{ uploadError }}</div>
    <div v-for="(par, i) in paragraphs" :key="par.id" class="para-block">
      <div class="para-toolbar">
        <span class="para-no">第 {{ i + 1 }} 块</span>

        <!-- 文本行内格式 -->
        <template v-if="par.type !== 'image' && par.type !== 'table'">
          <button class="tiny" :class="{ on: par.bold }" @click="toggle(par, 'bold')"><b>B</b></button>
          <button class="tiny" :class="{ on: par.italic }" @click="toggle(par, 'italic')"><i>I</i></button>
          <button class="tiny" :class="{ on: par.underline }" @click="toggle(par, 'underline')"><u>U</u></button>
        </template>
        <span v-else class="block-tag">{{ par.type === 'image' ? '🖼 图片' : '📊 表格' }}</span>

        <button class="tiny" @click="move(i, -1)" :disabled="i === 0">↑</button>
        <button class="tiny" @click="move(i, 1)" :disabled="i === paragraphs.length - 1">↓</button>
        <button class="tiny danger" @click="remove(i)">✖</button>
      </div>

      <!-- 文本段落 -->
      <textarea v-if="par.type !== 'image' && par.type !== 'table'"
                v-model="par.text" rows="4" class="para-text" :class="styleClass"
                :placeholder="'第 ' + (i + 1) + ' 段正文…'" @input="emitChange" />

      <!-- 图片块 -->
      <div v-else-if="par.type === 'image'" class="image-block">
        <div v-if="par.image && par.image.url" class="image-preview">
          <img :src="imageSrc(par.image.url)" alt="图片" />
          <button class="tiny" @click="replaceImage(par)">🔄 更换图片</button>
        </div>
        <div v-else class="image-upload" @click="uploadFor(par)">
          <div class="icon">🖼</div>
          <div>点击上传图片 (png/jpg)</div>
        </div>
        <input ref="imgInput" type="file" accept="image/*" hidden @change="onImagePicked" />
        <div class="caption-row">
          <span class="caption-num">图 {{ chapter }}.{{ figureIndex(i) }}</span>
          <input v-model="par.image.caption" class="caption-input"
                 :placeholder="'题注文字, 如: ' + sectionTitle" @input="emitChange" />
        </div>
      </div>

      <!-- 表格块 -->
      <div v-else-if="par.type === 'table'" class="table-block">
        <div class="table-size">
          <label>行 <input type="number" min="1" max="50" v-model.number="par.table.rows" @change="resize(par)" /></label>
          <label>列 <input type="number" min="1" max="20" v-model.number="par.table.cols" @change="resize(par)" /></label>
          <span class="hint-sm">调整行列数后, 原内容保留</span>
        </div>
        <table class="cell-grid">
          <tr v-for="(row, r) in par.table.cells" :key="r">
            <td v-for="(cell, c) in row" :key="c">
              <input v-model="par.table.cells[r][c]" placeholder="" @input="emitChange" />
            </td>
          </tr>
        </table>
        <div class="caption-row">
          <span class="caption-num">表 {{ chapter }}.{{ tableIndex(i) }}</span>
          <input v-model="par.table.caption" class="caption-input"
                 :placeholder="'题注文字, 如: ' + sectionTitle" @input="emitChange" />
        </div>
      </div>
    </div>

    <!-- 插入按钮 -->
    <div class="insert-row">
      <button class="primary" @click="addParagraph">➕ 段落</button>
      <button @click="addImage">🖼 图片</button>
      <button @click="addTable">📊 表格</button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onBeforeUnmount } from 'vue'
import { getPrivateImageUrl, uploadProjectImage } from '../api'

const props = defineProps({
  paragraphs: { type: Array, required: true },
  projectId: { type: String, required: true },
  styleClass: { type: String, default: '' },
  sectionTitle: { type: String, default: '' },
  chapter: { type: Number, default: 0 },   // 所属章号(题注自动编号用)
})
const emit = defineEmits(['change'])

const imgInput = ref(null)
const imageUrls = reactive(new Map())
const uploadError = ref('')
let pendingTarget = null

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
  let n = 0
  for (let k = 0; k <= i; k++) {
    if (props.paragraphs[k]?.type === 'image') n += 1
  }
  return n
}

/** 该块在本章中的表序号 */
function tableIndex(i) {
  let n = 0
  for (let k = 0; k <= i; k++) {
    if (props.paragraphs[k]?.type === 'table') n += 1
  }
  return n
}

function emitChange() { emit('change') }

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
    id: `p${Date.now()}`, type: 'paragraph', text: '',
    bold: false, italic: false, underline: false,
  })
  emit('change')
}

function addImage() {
  props.paragraphs.push({
    id: `img${Date.now()}`, type: 'image',
    image: { url: '', caption: '' },
  })
  emit('change')
}

function addTable() {
  const rows = 3, cols = 3
  props.paragraphs.push({
    id: `tbl${Date.now()}`, type: 'table',
    table: {
      rows, cols,
      cells: Array.from({ length: rows }, () => Array(cols).fill('')),
      caption: '',
    },
  })
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
  pendingTarget = par
  imgInput.value?.click()
}

function replaceImage(par) {
  pendingTarget = par
  imgInput.value?.click()
}

async function onImagePicked(e) {
  const f = e.target.files?.[0]
  e.target.value = ''
  const target = pendingTarget
  pendingTarget = null
  if (!f || !target) return
  uploadError.value = ''
  try {
    const { url } = await uploadProjectImage(props.projectId, f)
    target.image.url = url
    emit('change')
  } catch (err) {
    uploadError.value = '图片上传失败: ' + err.message
  }
}

onBeforeUnmount(() => {
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

.insert-row { display: flex; gap: 10px; margin-top: 4px; flex-wrap: wrap; }
@media (max-width: 600px) {
  .para-toolbar { flex-wrap: wrap; }
  .para-no { flex-basis: 100%; }
  .table-size { flex-wrap: wrap; gap: 8px; }
  .cell-grid { min-width: 520px; }
  .table-block { overflow-x: auto; }
}
</style>
