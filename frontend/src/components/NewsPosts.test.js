import {test} from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import {fileURLToPath} from 'node:url'
import {build} from 'esbuild'

// Exercise event handlers without a browser or real API/provider requests.
// esbuild is already supplied by Vite; no test dependencies are added.
const bundle=await build({
  entryPoints:[fileURLToPath(new URL('./NewsPosts.jsx',import.meta.url))],
  bundle:true,write:false,format:'cjs',jsxFactory:'React.createElement',
  plugins:[{name:'news-test-mocks',setup(builder){
    builder.onResolve({filter:/^react$|cloudflareApi$/},args=>({path:args.path==='react'?'React':'api',namespace:'mock'}))
    builder.onLoad({filter:/.*/,namespace:'mock'},args=>({contents:`module.exports=globalThis.${args.path}`}))
    builder.onLoad({filter:/\.css$/},()=>({contents:'',loader:'js'}))
  }}],
})
const config={enabled:false,run_at:'09:00',timezone_offset_minutes:330,posts_per_day:10,fetch_budget:6,channel:'demo',brand:'Demo',categories:[]}
const nodes=node=>node&&typeof node==='object'?[node,...(node.children||[]).flatMap(nodes)]:[]
const text=node=>node.children.filter(value=>typeof value==='string').join('')
const settle=()=>new Promise(resolve=>setImmediate(resolve))

async function harness({post=async()=>({id:'job'}),popupBlocked=false}={}) {
  let cursor=0
  const slots=[],calls=[],opened=[],redirects=[]
  const popup={opener:{},location:{href:''},closed:false,close(){this.closed=true}}
  const data={config:{...config},channels:[{slug:'demo',name:'Demo',instagram_connected:true},{slug:'other',name:'Other',instagram_connected:false}],posts:[
    {id:'approved',channel:'demo',status:'approved'},
    {id:'review',channel:'demo',status:'awaiting_approval'},
    {id:'uncertain',channel:'demo',status:'uncertain'},
  ]}
  const React={
    createElement:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity)}),
    useState(initial){const index=cursor++;if(!(index in slots))slots[index]=initial;return[slots[index],value=>slots[index]=typeof value==='function'?value(slots[index]):value]},
    useRef(initial){const index=cursor++;return slots[index]??={current:initial}},
    useCallback:callback=>callback,useEffect:()=>{},
  }
  const context={module:{exports:{}},React,URL,
    crypto:{randomUUID:()=> '12345678-1234-1234-1234-123456789abc'},
    window:{confirm:()=>true,open:(...args)=>{opened.push(args);return popupBlocked?null:popup},location:{assign:url=>redirects.push(url)}},
    api:{safeMediaUrl:()=>'',cloudflareRequest:async()=>data,postCloudflare:async(path,body={},options)=>{calls.push({path,body,options});return post(path,body,options)}},
  }
  vm.runInNewContext(bundle.outputFiles[0].text,context)
  const render=()=>{cursor=0;return context.module.exports.default({active:false})}
  const button=label=>nodes(render()).find(node=>node.type==='button'&&text(node)===label)
  const changeBrand=value=>nodes(render()).find(node=>node.type==='input'&&node.props.maxLength===35).props.onChange({target:{value}})
  const save=async()=>{nodes(render()).find(node=>node.type==='form').props.onSubmit({preventDefault(){}});await settle()}
  await button('Refresh news').props.onClick()
  return{calls,opened,redirects,popup,context,render,button,changeBrand,save}
}

test('connect uses the selected channel and Meta only, with isolated popup',async()=>{
  const h=await harness({post:async()=>({authorization_url:'https://example.com/instagram'})})
  nodes(h.render()).find(node=>node.type==='select').props.onChange({target:{value:'other'}})
  assert.equal(h.button('Connect Instagram').props.type,'button')
  h.button('Connect Instagram').props.onClick();await settle()
  assert.deepEqual(h.calls.map(call=>call.path),['/channels/other/connect/meta'])
  assert.equal(h.popup.opener,null)
  assert.equal(h.popup.location.href,'https://example.com/instagram')
  assert.equal(h.opened.length,1)
})

test('connect falls back to current tab when popups are blocked',async()=>{
  const h=await harness({popupBlocked:true,post:async()=>({authorization_url:'https://example.com/instagram'})})
  h.button('Connect Instagram').props.onClick();await settle()
  assert.deepEqual(h.redirects,['https://example.com/instagram'])
})

test('connect rejects unsafe URLs and surfaces missing-key errors without navigation',async()=>{
  for(const response of ['unsafe','missing-key']){
    const h=await harness({post:async()=>{if(response==='missing-key')throw Error('Main key not found');return{authorization_url:'javascript:alert(1)'}}})
    h.button('Connect Instagram').props.onClick();await settle()
    assert.equal(h.popup.closed,true)
    assert.equal(h.popup.location.href,'')
    assert.ok(nodes(h.render()).some(node=>node.props.role==='alert'&&text(node).includes(response==='missing-key'?'Main key not found':'Invalid connection URL')))
  }
})

test('brand is limited to 35 characters, and saving sends the full configuration',async()=>{
  const h=await harness()
  h.changeBrand('x'.repeat(36));await h.save()
  assert.equal(h.calls.length,0)
  h.changeBrand('x'.repeat(35))
  await h.button('Refresh news').props.onClick()
  assert.ok(nodes(h.render()).some(node=>node.props.value==='x'.repeat(35)))
  await h.save()
  assert.equal(h.calls[0].path,'/news/config')
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0].body)),{...config,brand:'x'.repeat(35)})
})

test('generation retries retain the 32-hex idempotency key and original channel',async()=>{
  let attempts=0
  const h=await harness({post:async()=>{if(++attempts===1)throw Error('Timeout');return{id:'job'}}})
  h.button('Generate one news post').props.onClick();await settle()
  nodes(h.render()).find(node=>node.type==='select').props.onChange({target:{value:'other'}})
  h.button('Retry same generation').props.onClick();await settle()
  assert.equal(h.calls.length,2)
  assert.equal(h.calls[0].path,'/news/run')
  assert.match(h.calls[0].options.headers['Idempotency-Key'],/^[a-f0-9]{32}$/)
  assert.equal(h.calls[0].options.headers['Idempotency-Key'],h.calls[1].options.headers['Idempotency-Key'])
  assert.equal(h.calls[1].body.channel,'demo')
})

test('approval is separate from publishing; confirmation and uncertain-result lock prevent duplicates',async()=>{
  const h=await harness({post:async path=>{if(path.endsWith('/publish'))throw Error('Timeout');return{}}})
  h.button('Approve carousel').props.onClick();await settle()
  assert.equal(h.calls[0].path,'/news/review/approve')
  assert.equal(h.calls[0].body.revision,1)
  assert.equal(h.calls.length,1)
  assert.equal(nodes(h.render()).filter(node=>node.type==='button'&&text(node)==='Publish to Instagram…').length,1)
  h.context.window.confirm=()=>false
  h.button('Publish to Instagram…').props.onClick();await settle()
  assert.equal(h.calls.length,1)
  h.context.window.confirm=()=>true
  h.button('Publish to Instagram…').props.onClick();await settle()
  assert.equal(h.calls[1].path,'/news/approved/publish')
  assert.equal(Object.keys(h.calls[1].body).length,0)
  assert.equal(h.button('Publish to Instagram…').props.disabled,true)
})
