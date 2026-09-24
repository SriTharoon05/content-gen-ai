import { test } from 'node:test'
import assert from 'node:assert/strict'
import { formatGenerationTime } from './generationTime.js'

test('completed duration includes hours and stays fixed', () => {
  assert.equal(formatGenerationTime({ status: 'complete', elapsed_seconds: 3661 }), '1h 1m 1s')
})
test('live elapsed time and failure are distinguished', () => {
  assert.equal(formatGenerationTime({ status: 'running', started_at: '2026-09-23T00:00:00Z' }, Date.parse('2026-09-23T00:02:03Z')), '2m 3s · elapsed')
  assert.equal(formatGenerationTime({ status: 'failed', elapsed_seconds: 65 }), '1m 5s · failed')
})
test('missing, invalid and future timing are safe', () => {
  assert.equal(formatGenerationTime(null), 'Not recorded')
  assert.equal(formatGenerationTime({ status: 'running', started_at: 'bad' }), 'Not recorded')
  assert.equal(formatGenerationTime({ status: 'complete', elapsed_seconds: -2 }), '0m 0s')
})
