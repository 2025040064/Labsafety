const assert = require('node:assert/strict')
const base = 'http://127.0.0.1:8765'
global.wx = {
  async request(options) {
    try {
      const response = await fetch(options.url, {
        method: options.method, headers: options.header,
        body: options.method === 'POST' ? JSON.stringify(options.data) : undefined
      })
      options.success({ statusCode: response.status, data: await response.json() })
    } catch (error) {
      options.fail(error)
    }
  }
}
const { request } = require('../../miniprogram/api')
const post = (path, data) => request(path, { method: 'POST', data })
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

async function run(action, extra = {}, cancel = false) {
  const job = await post('/api/jobs', {
    action, estimators: 10, trials: 2, algorithm: 'random', timeout: 20, ...extra
  })
  if (cancel) await post('/api/jobs/' + job.id + '/cancel', {})
  for (let i = 0; i < 200; i++) {
    const detail = await request('/api/jobs/' + job.id)
    if (!['running', 'cancelling'].includes(detail.status)) {
      assert.equal(detail.status, cancel ? 'cancelled' : 'completed', detail.error || detail.result?.failure_reason)
      console.log(action, detail.status, detail.result?.id || detail.result?.dataset_id || '', detail.events.length)
      return detail.result
    }
    await sleep(300)
  }
  throw new Error('Timed out: ' + action)
}

async function main() {
  assert.equal((await fetch(base + '/api/runs')).status, 403)
  assert.equal((await request('/api/me')).role, 'admin')
  const before = await request('/api/runs')
  assert(before.some(x => x.id.startsWith('IND-')))
  const old = before.filter(x => x.id.startsWith('EXP-') && x.status === 'completed').at(-1)
  assert(old)
  const zip = await fetch(base + '/api/runs/' + old.id + '/export', {
    headers: { 'X-Lab-Local': '1' }
  })
  assert.equal(zip.status, 200)
  assert.equal(Buffer.from(await zip.arrayBuffer()).subarray(0, 2).toString(), 'PK')
  await run('prepare')
  const a = await run('train')
  const b = await run('optimize')
  assert.equal((await request('/api/compare?id=' + a.id + '&id=' + b.id)).runs.length, 2)
  await run('industrial_run', { dataset: 'ai4i_2020', algorithm: 'baseline' })
  await run('optimize', { trials: 128 }, true)
  const loop = await run('loop_create', { manual: true, authorize: false })
  assert.equal(loop.hpo_attempts, 0)
  assert.equal(loop.authorized_policy_sha256, null)
  await assert.rejects(post('/api/jobs', { action: 'promote', id: loop.id }), /confirmation/)
  console.log('HTTP smoke passed')
}

main().catch(error => { console.error(error); process.exitCode = 1 })
