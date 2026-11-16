import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'

test('AI document picker adapts actual array response and preserves API failures', async () => {
  const old=globalThis.fetch
  const server=await createServer({configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),
    server:{middlewareMode:true,ws:false},appType:'custom'})
  try {
    const {getDocuments}=await server.ssrLoadModule('/src/features/teaching/ai/api.ts')
    globalThis.fetch=async ()=>new Response(JSON.stringify([{id:'9',lesson_id:'1',original_name:'test.txt'}]),
      {headers:{'Content-Type':'application/json'}})
    const page=await getDocuments('1')
    assert.equal(page.items[0].id,'9'); assert.equal(page.total,1)
    globalThis.fetch=async ()=>new Response(JSON.stringify({message:'denied'}),{status:403,headers:{'Content-Type':'application/json'}})
    await assert.rejects(getDocuments('1'),/denied/)
    const {login}=await server.ssrLoadModule('/src/shared/auth/session.ts')
    const {writeDraft,readDraft}=await server.ssrLoadModule('/src/features/teaching/ai/draft.ts')
    const previousStorage=globalThis.sessionStorage
    const values=new Map()
    globalThis.sessionStorage={getItem:(key)=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)}
    try {
      let account='A'
      globalThis.fetch=async(url)=>new Response(JSON.stringify(url.endsWith('/auth/login')?
        {access_token:account}:{id:account,role:'TEACHER'}),{headers:{'Content-Type':'application/json'}})
      await login({email:'A',password:'test'})
      writeDraft('1',{kind:'quiz',generation_id:'9',items:[],saved_count:0})
      assert.equal(readDraft('1').generation_id,'9')
      account='B';await login({email:'B',password:'test'})
      assert.equal(readDraft('1'),null)
    } finally {globalThis.sessionStorage=previousStorage}
  } finally {globalThis.fetch=old;await server.close()}
})
