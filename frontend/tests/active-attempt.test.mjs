import assert from 'node:assert/strict'
import { test } from 'node:test'
import { QueryClient, QueryObserver } from '@tanstack/react-query'
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'

// A POST inside a query must not be re-executed by background read triggers.
test('reconnect after submission cannot silently create a new active attempt', async () => {
  const oldFetch = globalThis.fetch
  const server = await createServer({ configFile:false, root:fileURLToPath(new URL('..',import.meta.url)),
    server:{middlewareMode:true,ws:false},appType:'custom' })
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}})
  let observer, unsubscribe
  try {
    let starts=0
    globalThis.fetch=async()=> {
      starts++
      return new Response(JSON.stringify({id:String(starts),quiz_id:'30',student_id:'4',status:'IN_PROGRESS',total_questions:1,started_at:'2026-11-16T00:00:00Z'}),
        {headers:{'Content-Type':'application/json'}})
    }
    const {activeAttemptOptions}=await server.ssrLoadModule('/src/features/learning/attempts/activeAttempt.ts')
    const options=activeAttemptOptions('30')
    observer=new QueryObserver(client,options)
    unsubscribe=observer.subscribe(()=>{})
    await client.fetchQuery(options)
    // This is TanStack's real reconnect path, not a hand-written retry simulation.
    client.getQueryCache().find({queryKey:options.queryKey}).onOnline()
    await new Promise(setImmediate)
    assert.equal(starts,1,'background reconnect must not repeat POST /attempts')
    unsubscribe();unsubscribe=undefined
    client.getQueryCache().find({queryKey:options.queryKey}).invalidate()
    observer=new QueryObserver(client,options)
    unsubscribe=observer.subscribe(()=>{})
    await new Promise(setImmediate)
    assert.equal(starts,1,'mount must not repeat a side-effect query before cleanup')
    // Entering quiz intentionally after leaving removes old entry and starts a real new attempt.
    unsubscribe();unsubscribe=undefined
    client.removeQueries({queryKey:options.queryKey,exact:true})
    await client.fetchQuery(options)
    assert.equal(starts,2)
  } finally {
    unsubscribe?.();client.clear();globalThis.fetch=oldFetch;await server.close()
  }
})
