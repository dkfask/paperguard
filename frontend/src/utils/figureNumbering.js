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

export function computeManuscriptImageLocations(sections, chapterNumbers) {
  const all = []
  const chapterCounters = new Map()
  let fallbackFigure = 0
  for (const section of sections || []) {
    const mappedChapter = chapterNumbers?.[section.key]
    const chapter = Number.isInteger(mappedChapter) && mappedChapter > 0 ? mappedChapter : 0
    for (let index = 0; index < (section.paragraphs || []).length; index += 1) {
      const block = section.paragraphs[index]
      if (block.type !== 'image' || !block.id) continue
      let figureNumber
      if (chapter > 0) {
        figureNumber = (chapterCounters.get(chapter) || 0) + 1
        chapterCounters.set(chapter, figureNumber)
      } else {
        fallbackFigure += 1
        figureNumber = fallbackFigure
      }
      all.push({
        sectionKey: section.key,
        sectionTitle: section.title || section.key,
        blockId: block.id,
        caption: block.image?.caption || '',
        position: index + 1,
        figureLabel: chapter > 0 ? `图 ${chapter}.${figureNumber}` : `第 ${figureNumber} 图`,
        isSmartArt: Boolean(block.image?.smartart && Object.keys(block.image.smartart).length),
      })
    }
  }
  const unverifiedRaster = all.filter((item) => !item.isSmartArt)
  return unverifiedRaster.length ? unverifiedRaster : all
}
