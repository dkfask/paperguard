import assert from 'node:assert/strict'
import { describe, it, beforeEach } from 'node:test'
import {
  PRIVACY_ACCEPT_KEY,
  PRIVACY_VERSION,
  acceptPrivacy,
  applySharePolicy,
  canShareAppMessage,
  isPrivacyAccepted,
  isSensitiveShareRoute,
  privacyNoticeSummary,
} from '../src/utils/privacy.ts'
import { applyMessageTabBadge, tabBarBadgeText } from '../src/utils/tab-badge.ts'

const storage = new Map<string, unknown>()

describe('MP privacy and share policy', () => {
  beforeEach(() => {
    storage.clear()
  })

  it('requires accepting the current privacy version before login continues', () => {
    const store = {
      getStorageSync: (key: string) => storage.get(key),
      setStorageSync: (key: string, value: unknown) => {
        storage.set(key, value)
      },
    }
    assert.equal(isPrivacyAccepted(store), false)
    acceptPrivacy(store, () => '2026-09-16T00:00:00Z')
    assert.equal(isPrivacyAccepted(store), true)
    const saved = storage.get(PRIVACY_ACCEPT_KEY) as { version: string }
    assert.equal(saved.version, PRIVACY_VERSION)
  })

  it('states collection scope without phone or location', () => {
    const summary = privacyNoticeSummary()
    assert.match(summary, /登录凭证/)
    assert.match(summary, /账号绑定关系/)
    assert.match(summary, /消息推送标识/)
    assert.match(summary, /不会采集：手机号、地理位置/)
  })

  it('allows sharing only the home tab and treats submission/review as sensitive', () => {
    assert.equal(canShareAppMessage('/pages/tasks/index'), true)
    assert.equal(canShareAppMessage('pages/tasks/index'), true)
    assert.equal(canShareAppMessage('/pages/login/index'), false)
    assert.equal(isSensitiveShareRoute('/pages/submissions/detail?id=s1'), true)
    assert.equal(isSensitiveShareRoute('/pages/review/index?id=s1'), true)
    assert.equal(isSensitiveShareRoute('/pages/submit/index'), true)
  })

  it('hides the share menu on submission and review pages', () => {
    const calls: string[] = []
    const host = {
      hideShareMenu: () => calls.push('hide'),
      showShareMenu: () => calls.push('show'),
    }
    applySharePolicy('/pages/submissions/detail', host)
    applySharePolicy('/pages/review/index', host)
    applySharePolicy('/pages/tasks/index', host)
    assert.deepEqual(calls, ['hide', 'hide', 'show'])
  })
})

describe('MP message tab badge', () => {
  it('formats unread counts and clears at zero', () => {
    assert.equal(tabBarBadgeText(0), null)
    assert.equal(tabBarBadgeText(3), '3')
    assert.equal(tabBarBadgeText(100), '99+')
  })

  it('sets or removes the messages tab badge', () => {
    const calls: Array<Record<string, unknown>> = []
    const host = {
      setTabBarBadge: (options: { index: number; text: string }) => calls.push({ type: 'set', ...options }),
      removeTabBarBadge: (options: { index: number }) => calls.push({ type: 'remove', ...options }),
    }
    applyMessageTabBadge(4, host)
    applyMessageTabBadge(0, host)
    assert.deepEqual(calls, [
      { type: 'set', index: 1, text: '4' },
      { type: 'remove', index: 1 },
    ])
  })
})
