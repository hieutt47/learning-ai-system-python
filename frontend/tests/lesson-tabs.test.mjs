import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'

test('lesson tabs require persisted BIGINT; new lesson cannot generate quiz/AI links', async () => {
  const server = await createServer({configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),
    server:{middlewareMode:true,ws:false},appType:'custom'})
  try {
    const {lessonTabs} = await server.ssrLoadModule('/src/shared/routing/lessonTabs.ts')
    for (const id of ['new', '', '0', 'abc', '01', '9223372036854775808']) {
      assert.deepEqual(lessonTabs({lessonId:id}),[])
    }
    const tabs=lessonTabs({lessonId:'42'})
    assert.deepEqual(tabs.map(t=>t.to),[
      '/teaching/lessons/42', '/teaching/lessons/42/quizzes', '/teaching/lessons/42/ai',
    ])
  } finally { await server.close() }
})
