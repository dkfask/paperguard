import { describe, expect, it } from 'vitest'
import { computeManuscriptImageLocations, countChapterFiguresBefore, countChapterTablesBefore } from '../src/utils/figureNumbering.js'

describe('figure numbering offsets', () => {
  it('counts images from earlier sections in the same chapter only', () => {
    const sections = [
      { key: 'chapter-3', type: 'heading1', paragraphs: [] },
      { key: 'chapter-3-1', type: 'heading2', paragraphs: [{ type: 'image' }] },
      { key: 'chapter-3-2', type: 'heading2', paragraphs: [{ type: 'image' }, { type: 'image' }] },
      { key: 'chapter-4', type: 'heading1', paragraphs: [] },
      { key: 'chapter-4-1', type: 'heading2', paragraphs: [{ type: 'image' }] },
    ]
    const chapterNumbers = {
      'chapter-3': 3,
      'chapter-3-1': 3,
      'chapter-3-2': 3,
      'chapter-4': 4,
      'chapter-4-1': 4,
    }

    expect(countChapterFiguresBefore(sections, chapterNumbers, 'chapter-3-1')).toBe(0)
    expect(countChapterFiguresBefore(sections, chapterNumbers, 'chapter-3-2')).toBe(1)
    expect(countChapterFiguresBefore(sections, chapterNumbers, 'chapter-4-1')).toBe(0)
  })

  it('counts tables from preceding sections without mixing chapters or figures', () => {
    const sections = [
      { key: 'c3', paragraphs: [{ type: 'table' }, { type: 'image' }] },
      { key: 'c3a', paragraphs: [{ type: 'table' }, { type: 'table' }] },
      { key: 'c3b', paragraphs: [{ type: 'table' }] },
      { key: 'c4', paragraphs: [] },
    ]
    const chapters = { c3: 3, c3a: 3, c3b: 3, c4: 4 }
    expect(countChapterTablesBefore(sections, chapters, 'c3b')).toBe(3)
    expect(countChapterTablesBefore(sections, chapters, 'c4')).toBe(0)
    expect(countChapterFiguresBefore(sections, chapters, 'c3b')).toBe(1)
  })

  it('numbers manuscript images across chapters and keeps unverified screenshots when mixed with SmartArt', () => {
    const sections = [
      {
        key: 'chapter-3',
        type: 'heading1',
        title: '第 3 章 系统分析',
        paragraphs: [
          { id: 'img-sa', type: 'image', image: { caption: '架构图', smartart: { dm: '<d/>' } } },
        ],
      },
      {
        key: 'chapter-3-1',
        type: 'heading2',
        title: '3.1 架构实现',
        paragraphs: [
          { id: 'img-shot', type: 'image', image: { url: '/media/uploads/shot.png', caption: '运行截图' } },
        ],
      },
    ]
    const chapters = { 'chapter-3': 3, 'chapter-3-1': 3 }
    expect(computeManuscriptImageLocations(sections, chapters)).toEqual([
      {
        sectionKey: 'chapter-3-1',
        sectionTitle: '3.1 架构实现',
        blockId: 'img-shot',
        caption: '运行截图',
        position: 1,
        figureLabel: '图 3.2',
        isSmartArt: false,
      },
    ])
  })
})
