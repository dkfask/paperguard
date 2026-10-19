import { nextTick, onBeforeUnmount, watch } from 'vue'

// Keeps modal keyboard navigation local and restores its opening control.
export function useDialogFocus(open, dialog, close, preferredFocus) {
  let returnTarget
  const focusable = () => Array.from(dialog.value?.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  ) || []).filter(el => !el.closest('[hidden], [inert]') && getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden')

  function handleKey(event) {
    if (!open.value || !dialog.value) return
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      close()
      return
    }
    if (event.key !== 'Tab') return
    const items = focusable()
    const first = items[0] || dialog.value
    const last = items[items.length - 1] || dialog.value
    if (!items.length || !dialog.value.contains(document.activeElement) || (event.shiftKey && document.activeElement === first) || (!event.shiftKey && document.activeElement === last)) {
      event.preventDefault()
      ;(event.shiftKey ? last : first).focus()
    }
  }

  function restoreFocus() {
    document.removeEventListener('keydown', handleKey, true)
    if (returnTarget?.isConnected) returnTarget.focus()
    returnTarget = undefined
  }

  watch(open, async visible => {
    if (!visible) { restoreFocus(); return }
    returnTarget = document.activeElement
    await nextTick()
    if (!open.value) return
    document.addEventListener('keydown', handleKey, true)
    const target = preferredFocus?.value || focusable()[0] || dialog.value
    target?.focus()
  }, { immediate: true })

  onBeforeUnmount(restoreFocus)
}
