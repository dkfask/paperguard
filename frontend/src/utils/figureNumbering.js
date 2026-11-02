function countChapterBlocksBefore(sections, chapterNumbers, sectionKey, blockType) {
  const chapter = chapterNumbers?.[sectionKey]
  if (chapter === undefined) return 0

  let count = 0
  for (const section of sections || []) {
    if (section.key === sectionKey) break
    if (chapterNumbers[section.key] !== chapter) continue
    count += (section.paragraphs || []).filter((block) => block.type === blockType).length
  }
  return count
}

export function countChapterFiguresBefore(sections, chapterNumbers, sectionKey) {
  return countChapterBlocksBefore(sections, chapterNumbers, sectionKey, 'image')
}

export function countChapterTablesBefore(sections, chapterNumbers, sectionKey) {
  return countChapterBlocksBefore(sections, chapterNumbers, sectionKey, 'table')
}
