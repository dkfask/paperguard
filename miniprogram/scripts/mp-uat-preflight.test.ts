import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import {
  isPublicHttpsApiBase,
  wechatAppIdReady,
  wechatUatBlockers,
  wechatUatReady,
} from '../src/utils/wechat-uat-preflight.ts'

describe('WeChat legal-domain preflight', () => {
  it('accepts a public https origin and rejects localhost or placeholders', () => {
    assert.equal(isPublicHttpsApiBase('https://mp.paperguard.edu.cn/api').ok, true)
    assert.equal(isPublicHttpsApiBase('http://mp.paperguard.edu.cn').ok, false)
    assert.equal(isPublicHttpsApiBase('https://localhost/api').ok, false)
    assert.equal(isPublicHttpsApiBase('https://127.0.0.1/api').ok, false)
    assert.equal(isPublicHttpsApiBase('https://paperguard.example.com').ok, false)
    assert.equal(isPublicHttpsApiBase('https://paperguard.example.edu').ok, false)
    assert.equal(isPublicHttpsApiBase('https://192.168.1.8/api').ok, false)
    assert.equal(isPublicHttpsApiBase('').ok, false)
  })

  it('rejects placeholder mini-program app ids', () => {
    assert.equal(wechatAppIdReady(''), false)
    assert.equal(wechatAppIdReady('__UNI__PAPERGUARD'), false)
    assert.equal(wechatAppIdReady('wx1234567890abcdef'), true)
  })
})

describe('WeChat UAT blockers', () => {
  it('stays blocked without secrets, domain, or device even if code is ready', () => {
    const blockers = wechatUatBlockers({})
    assert.equal(wechatUatReady(blockers), false)
    assert.deepEqual(
      blockers.filter((item) => item.status === 'blocked').map((item) => item.id),
      ['UAT-MP-DOMAIN', 'UAT-MP-APPID', 'UAT-MP-SERVER', 'UAT-MP-SUBSCRIBE', 'UAT-MP-DEVICE'],
    )
  })

  it('still blocks device UAT after domain and env look ready', () => {
    const blockers = wechatUatBlockers({
      apiBaseUrl: 'https://mp.paperguard.edu.cn',
      mpAppId: 'wx1234567890abcdef',
      wechatAppIdConfigured: true,
      wechatAppSecretConfigured: true,
      mpBindingSecretConfigured: true,
      subscribeTemplatesConfigured: true,
    })
    assert.equal(blockers.find((item) => item.id === 'UAT-MP-DOMAIN')?.status, 'ready')
    assert.equal(blockers.find((item) => item.id === 'UAT-MP-DEVICE')?.status, 'blocked')
    assert.equal(wechatUatReady(blockers), false)
  })
})
