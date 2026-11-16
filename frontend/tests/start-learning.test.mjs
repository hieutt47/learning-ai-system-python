import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'

test('learning entry resolves lessons independently of missing enrollment outline', async () => {
  const original=globalThis.fetch
  const server=await createServer({ configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),
    server:{middlewareMode:true,ws:false},appType:'custom' })
  try {
    globalThis.fetch=async (url) => {
      assert.equal(url,'/api/v1/courses/20/lessons')
      return new Response(JSON.stringify([
        { id:'12',course_id:'20',title:'Second',order_index:2 },
        { id:'11',course_id:'20',title:'First',order_index:1 },
      ]),{headers:{'Content-Type':'application/json'}})
    }
    const {learningDestination}=await server.ssrLoadModule('/src/features/learning/startLearning.ts')
    assert.equal(await learningDestination('20'),'/learn/20/lessons/11')
    globalThis.fetch=async()=>new Response('[]',{headers:{'Content-Type':'application/json'}})
    assert.equal(await learningDestination('20'),null)
    globalThis.fetch=async()=>new Response(JSON.stringify({message:'not enrolled'}),{status:403,headers:{'Content-Type':'application/json'}})
    await assert.rejects(learningDestination('20'),/not enrolled/)
  } finally {globalThis.fetch=original;await server.close()}
})
