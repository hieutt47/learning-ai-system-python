import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'

for (const lost of [false, true]) {
  test(`atomic draft save retries identical snapshot after lost response=${lost}`, async () => {
    const original = globalThis.fetch
    const server = await createServer({ configFile: false, root: fileURLToPath(new URL('..', import.meta.url)),
      server: { middlewareMode: true, ws: false }, appType: 'custom' })
    const receipts = new Map(), bodies = []
    let inserts = 0, first = true
    try {
      globalThis.fetch = async (url, options) => {
        assert.equal(url, '/api/v1/quizzes/30/draft')
        assert.equal(options.method, 'PUT')
        const body = JSON.parse(options.body)
        bodies.push(body)
        if (!receipts.has(body.request_id)) {
          inserts++
          receipts.set(body.request_id, { id: '30', lesson_id: '10', title: body.title,
            status: 'DRAFT', questions: body.questions.map((q,i) => ({ ...q, id: q.id ?? String(i+10) })) })
        }
        if (lost && first) { first = false; throw new Error('connection lost AFTER commit') }
        return new Response(JSON.stringify(receipts.get(body.request_id)), { headers: { 'Content-Type': 'application/json' } })
      }
      const { createDraftSaver } = await server.ssrLoadModule('/src/features/teaching/quizzes/saveQuizDraft.ts')
      const save = createDraftSaver()
      const draft = [{ content: 'Question', orderIndex: 0, options: [
        { content: 'A', isCorrect: true }, { content: 'B', isCorrect: false },
      ] }]
      if (lost) await assert.rejects(save('30', 'Quiz', draft), /Không kết nối/)
      const saved = await save('30', 'Quiz', draft)
      assert.equal(saved.questions.length, 1)
      assert.equal(inserts, 1)
      if (lost) assert.deepEqual(bodies[0], bodies[1])
      assert.equal(bodies[0].questions[0].explanation, null)
      await save('30', 'Changed', saved.questions)
      assert.notEqual(bodies.at(-1).request_id, bodies[0].request_id)
      assert.equal(bodies.at(-1).questions[0].id, '10')
    } finally { globalThis.fetch = original; await server.close() }
  })
}
