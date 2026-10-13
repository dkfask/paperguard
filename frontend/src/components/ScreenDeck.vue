<script>
import { Comment, Fragment, Text, cloneVNode, defineComponent, h, ref, useId } from 'vue'
import { useI18n } from '../i18n'

function flatten(nodes) {
  return nodes.flatMap(node => node.type === Fragment && Array.isArray(node.children) ? flatten(node.children) : [node])
    .filter(node => node.type !== Comment && !(node.type === Text && !String(node.children).trim()))
}
function heading(node) {
  if (/^h[1-4]$/.test(String(node.type))) return text(node)
  return Array.isArray(node.children) ? node.children.map(heading).find(Boolean) : ''
}
function text(node) {
  if (node.props?.['aria-hidden'] === true || node.props?.['aria-hidden'] === 'true') return ''
  if (typeof node.children === 'string') return node.children
  return Array.isArray(node.children) ? node.children.map(text).join('') : ''
}
// Keep panes mounted so unsaved input and editor instances survive a switch.
export default defineComponent({
  name: 'ScreenDeck',
  props: { labels: { type: Array, default: () => [] }, modelValue: { type: Number, default: undefined } },
  emits: ['update:modelValue'],
  setup(props, { slots, emit }) {
    const selected = ref(0), id = useId()
    const { t } = useI18n()
    const choose = index => { selected.value = index; emit('update:modelValue', index) }
    return () => {
      const children = flatten(slots.default?.() || [])
      const overlays = children.filter(node => node.type?.__name === 'BaseModal')
      const panes = children.filter(node => !overlays.includes(node))
      const active = Math.min(props.modelValue ?? selected.value, Math.max(0, panes.length - 1))
      const label = (pane, index) => props.labels[index] || heading(pane) || pane.props?.['aria-label'] || pane.props?.title || t('screen.section', { index: index + 1 })
      return h('div', { class: 'screen-deck', 'data-screen-deck': '' }, [
        slots.header?.(),
        panes.length > 1 && h('nav', { class: 'screen-deck-tabs', role: 'tablist', 'aria-label': t('screen.sections') }, panes.map((pane, index) => h('button', {
          id: `${id}-tab-${index}`, type: 'button', role: 'tab', class: { active: index === active }, 'aria-selected': index === active,
          'aria-controls': `${id}-pane-${index}`, tabindex: index === active ? 0 : -1,
          onClick: () => choose(index),
          onKeydown: event => {
            const offset = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? panes.length - 1 : (index + offset + panes.length) % panes.length
            if (!offset && !['Home', 'End'].includes(event.key)) return
            event.preventDefault(); choose(next)
            event.currentTarget.parentElement.children[next]?.focus()
          },
        }, label(pane, index)))),
        h('div', { class: 'screen-deck-stage' }, panes.map((pane, index) => h('div', {
          id: `${id}-pane-${index}`, key: pane.key ?? index, role: 'tabpanel', 'aria-label': label(pane, index),
          'aria-labelledby': panes.length > 1 ? `${id}-tab-${index}` : undefined,
          class: ['screen-deck-pane', { 'screen-pane-active': index === active }], hidden: index !== active,
        }, [cloneVNode(pane)]))),
        ...overlays,
      ])
    }
  },
})
</script>
