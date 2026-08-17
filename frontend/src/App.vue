<template>
  <div>
    <header>
      <span class="logo">📄 PaperGuard</span>
      <span class="tagline">论文格式规范检查与自动修复</span>
    </header>
    <main>
      <!-- 上传与检查 -->
      <section class="card">
        <h2>1. 上传论文并检查</h2>
        <div
          class="upload-zone"
          :class="{ dragging }"
          @click="pickFile"
          @dragover.prevent="dragging = true"
          @dragleave="dragging = false"
          @drop.prevent="onDrop"
        >
          <div class="icon">📁</div>
          <div v-if="file">{{ file.name }}</div>
          <div v-else>点击选择或拖入 .docx 文件</div>
          <div class="hint">仅支持 .docx;文件仅在服务器临时处理,不保存论文内容</div>
        </div>
        <input ref="fileInput" type="file" accept=".docx" hidden @change="onPick" />

        <div class="form-row">
          <label for="pack">规则包</label>
          <select id="pack" v-model="pack">
            <option v-for="p in packs" :key="p" :value="p">{{ p }}</option>
          </select>
          <button class="primary" :disabled="!file || checking" @click="runCheck">
            {{ checking ? '检查中…' : '开始检查' }}
          </button>
        </div>
      </section>

      <div v-if="error" class="error-banner">⚠ {{ error }}</div>

      <!-- 报告 -->
      <section v-if="report" class="card">
        <h2>2. 检查结果</h2>
        <div class="summary-cards">
          <div class="summary-card total">
            <div class="num">{{ report.summary.total }}</div>
            <div class="label">问题总数</div>
          </div>
          <div class="summary-card error">
            <div class="num">{{ report.summary.error }}</div>
            <div class="label">错误</div>
          </div>
          <div class="summary-card warning">
            <div class="num">{{ report.summary.warning }}</div>
            <div class="label">警告</div>
          </div>
          <div class="summary-card info">
            <div class="num">{{ report.summary.info }}</div>
            <div class="label">提示</div>
          </div>
        </div>

        <div v-if="report.findings.length === 0" style="color: var(--muted)">
          ✅ 未发现格式问题,论文符合该规则包要求。
        </div>

        <div v-for="(f, i) in report.findings" :key="i" class="finding" :class="'severity-' + f.severity">
          <div class="head">
            <span class="rule-id">{{ f.rule_id }}</span>
            <span class="badge" :class="f.severity">
              {{ { error: '错误', warning: '警告', info: '提示' }[f.severity] }}
            </span>
          </div>
          <div class="message">{{ f.message }}</div>
          <div class="meta" v-if="f.location.paragraph_index !== null">
            段落 #{{ f.location.paragraph_index }}
          </div>
          <span class="snippet" v-if="f.location.text_snippet">"{{ f.location.text_snippet }}"</span>
          <button v-if="f.fix" class="fix-btn" :disabled="fixing" @click="runFix">
            🔧 自动修复 ({{ f.fix.action }} {{ f.fix.target }})
          </button>
        </div>
      </section>

      <!-- 预览 -->
      <section v-if="previewUrl" class="card">
        <h2>3. 文档预览</h2>
        <div class="form-row" style="margin-top: 0; margin-bottom: 12px">
          <button v-if="report && report.summary.total > 0" class="primary" :disabled="fixing" @click="runFix">
            {{ fixing ? '修复中…' : '🔧 一键修复并下载' }}
          </button>
        </div>
        <div class="docx-preview">
          <docx-preview :src="previewUrl" />
        </div>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import VueOfficeDocx from '@vue-office/docx'
import '@vue-office/docx/lib/index.css'
import { fetchPacks, checkDocx, fixDocx } from './api'

const DocxPreview = VueOfficeDocx

const file = ref(null)
const packs = ref([])
const pack = ref('GB-T-7713-1-2006')
const checking = ref(false)
const fixing = ref(false)
const report = ref(null)
const error = ref('')
const previewUrl = ref('')
const dragging = ref(false)
const fileInput = ref(null)

onMounted(async () => {
  try {
    packs.value = await fetchPacks()
    if (!packs.value.includes(pack.value) && packs.value.length) {
      pack.value = packs.value[0]
    }
  } catch (e) {
    error.value = e.message
  }
})

function pickFile() { fileInput.value?.click() }

function onPick(e) {
  const f = e.target.files?.[0]
  if (f) setFile(f)
}

function onDrop(e) {
  dragging.value = false
  const f = e.dataTransfer?.files?.[0]
  if (f) setFile(f)
}

function setFile(f) {
  if (!f.name.toLowerCase().endsWith('.docx')) {
    error.value = '只支持 .docx 文件'
    return
  }
  file.value = f
  error.value = ''
  report.value = null
  if (previewUrl.value) { URL.revokeObjectURL(previewUrl.value); previewUrl.value = '' }
  previewUrl.value = URL.createObjectURL(f)
}

async function runCheck() {
  if (!file.value) return
  checking.value = true
  error.value = ''
  try {
    report.value = await checkDocx(file.value, pack.value)
  } catch (e) {
    error.value = e.message
  } finally {
    checking.value = false
  }
}

async function runFix() {
  if (!file.value) return
  fixing.value = true
  error.value = ''
  try {
    const name = file.value.name.replace(/\.docx$/i, '') + '-fixed.docx'
    await fixDocx(file.value, pack.value, name)
  } catch (e) {
    error.value = e.message
  } finally {
    fixing.value = false
  }
}
</script>
