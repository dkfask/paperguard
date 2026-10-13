<template>
  <ScreenCollection :items="rows" :row-height="48" row-selector="tbody tr" body-class="table-scroll" v-slot="{ items: visibleRows }" class="data-table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column.key" :style="column.width ? { width: column.width } : undefined">
            {{ column.label }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, rowIndex) in visibleRows" :key="row[rowKey] || rowIndex">
          <td v-for="column in columns" :key="column.key">
            <slot :name="`cell-${column.key}`" :row="row" :value="row[column.key]">
              {{ formatValue(row[column.key]) }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </ScreenCollection></template>

<script setup>
import ScreenCollection from './ScreenCollection.vue'
defineProps({
  columns: { type: Array, required: true },
  rows: { type: Array, default: () => [] },
  rowKey: { type: String, default: 'id' },
})

function formatValue(value) {
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
}
</script>
