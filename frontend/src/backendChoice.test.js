import { test } from 'node:test'
import assert from 'node:assert/strict'
import { selectedBackend, backendToken, saveBackend } from './backendChoice.js'

test('backend selection persists without sharing tokens or changing running jobs', () => {
  const data = new Map()
  globalThis.localStorage = { getItem: k => data.get(k), setItem: (k,v) => data.set(k,v) }
  assert.equal(selectedBackend(), 'render')
  saveBackend('render','fake-render')
  saveBackend('cloudflare','fake-cloudflare')
  assert.equal(selectedBackend(),'cloudflare')
  assert.equal(backendToken(),'fake-cloudflare')
  assert.equal(backendToken('render'),'fake-render')
  assert.throws(()=>saveBackend('other','fake'), /Invalid backend/)
  saveBackend('render',backendToken('render'))
  assert.equal(backendToken(),'fake-render')
  assert.equal(backendToken('cloudflare'),'fake-cloudflare')
  delete globalThis.localStorage
})
