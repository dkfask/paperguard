<template>
  <div ref="root" class="screen-collection">
    <div ref="body" class="screen-collection-body" :class="bodyClass">
      <slot :items="visibleItems" :page="page" />
    </div>
    <nav v-if="paging && items.length" class="screen-pager" :aria-label="t('screen.pagination')">
      <span aria-live="polite">{{ t('screen.page', { page, pages: totalPages, count: items.length }) }}</span>
      <button type="button" :disabled="page === 1" :aria-label="t('screen.previous')" @click="page--">‹</button>
      <button type="button" :disabled="page === totalPages" :aria-label="t('screen.next')" @click="page++">›</button>
    </nav>
  </div>
</template>
<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from '../i18n'
const props = defineProps({ items: { type: Array, default: () => [] }, rowHeight: { type: Number, default: 76 }, bodyClass: String, rowSelector: String })
const { t } = useI18n()
const root = ref(null), body = ref(null), page = ref(1), capacity = ref(3), paging = ref(false)
const totalPages = computed(() => paging.value ? Math.max(1, Math.ceil(props.items.length / capacity.value)) : 1)
const visibleItems = computed(() => paging.value ? props.items.slice((page.value - 1) * capacity.value, page.value * capacity.value) : props.items)
let observer, frame, measuredWidth = 0, tallestRow = 0
function measure() {
  cancelAnimationFrame(frame)
  frame = requestAnimationFrame(() => {
    if (!body.value?.clientHeight) return
    paging.value = window.innerWidth > 768 && !root.value.closest('.screen-drawer')
    if (!paging.value) return
    if (body.value.clientWidth !== measuredWidth) {
      measuredWidth = body.value.clientWidth
      tallestRow = 0
    }
    const rows = [...(props.rowSelector ? body.value.querySelectorAll(props.rowSelector) : body.value.children)].filter(el => el.getClientRects().length)
    const measured = rows.map(el => el.getBoundingClientRect().height + parseFloat(getComputedStyle(el).marginBottom || '0'))
    const gap = parseFloat(getComputedStyle(body.value).rowGap) || 0
    // Keep the largest row measured at this width, so shorter pages cannot
    // repeatedly change capacity and move an item during editing.
    tallestRow = Math.max(props.rowHeight, tallestRow, ...measured, 1)
    const nextCapacity = Math.max(1, Math.floor((body.value.clientHeight + gap - 4 - (body.value.querySelector('thead')?.getBoundingClientRect().height || 0)) / (tallestRow + gap)))
    if (nextCapacity !== capacity.value) {
      const focusedRow = rows.findIndex(row => row.contains(document.activeElement))
      const anchor = (page.value - 1) * capacity.value + Math.max(0, focusedRow)
      capacity.value = nextCapacity
      page.value = Math.min(totalPages.value, Math.floor(anchor / nextCapacity) + 1)
    }
  })
}
watch(totalPages, pages => { if (page.value > pages) page.value = pages })
watch(() => props.items, () => { page.value = 1; tallestRow = 0; nextTick(measure) })
watch(() => props.items.length, () => nextTick(measure))
watch(page, () => nextTick(measure))
onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') { observer = new ResizeObserver(measure); observer.observe(body.value) }
  window.addEventListener('resize', measure)
  measure()
})
onBeforeUnmount(() => { observer?.disconnect(); window.removeEventListener('resize', measure); cancelAnimationFrame(frame) })
</script>
