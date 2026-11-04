"""Browser module regressions using Node, with no network or Docker dependency."""
from pathlib import Path
import shutil
import subprocess

import pytest


def run_node(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    result = subprocess.run(
        [node, '--experimental-vm-modules', '--input-type=module', '-e', script],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_clipboard_secure_api_and_lan_fallback_cleanup():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const text='  first\nsecond\t🌍\n', savedRange={};
let field, copied, focused=0, fallbackCalls=0, result=true;
const selection={rangeCount:1,getRangeAt:()=>({cloneRange:()=>savedRange}),
 removeAllRanges(){this.restored=[];},addRange(r){this.restored.push(r);}};
const navigator={clipboard:{writeText:async value=>{copied=value;}}};
const document={
 activeElement:{focus(){focused++;}}, body:{appendChild(el){field=el;}},
 createElement:()=>({style:{},focus(){},select(){},setSelectionRange(){},remove(){field=null;}}),
 execCommand(command){
  fallbackCalls++; assert.equal(command,'copy'); assert.equal(field.value,text);
  if(result instanceof Error)throw result;
  return result;
 }
};
const context=vm.createContext({navigator,document,window:{getSelection:()=>selection}});
const mod=new vm.SourceTextModule(readFileSync('static/js/clipboard.js','utf8'),{context});
await mod.link(()=>{});await mod.evaluate();
const copy=mod.namespace.copyText;
await copy(text);assert.equal(copied,text);assert.equal(fallbackCalls,0);
for(const clipboard of [undefined,{writeText:async()=>{throw new Error('Denied');}}]) {
 navigator.clipboard=clipboard;
 const pending=copy(text);
 if(!clipboard)assert.equal(fallbackCalls,1,'LAN copying must run within the click gesture');
 await pending;
}
navigator.clipboard=undefined;
for(result of [false,new Error('Blocked')])await assert.rejects(copy(text));
assert.equal(fallbackCalls,4);assert.equal(focused,4);assert.equal(field,null);
assert.deepEqual(selection.restored,[savedRange]);
''')


def test_chat_payload_scopes_folder_context_and_preserves_attachment_context():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const state={folderId:null,folderSystemPrompt:'Folder instructions',systemPrompt:'Global instructions'};
const context=vm.createContext({});
const policy=new vm.SourceTextModule(readFileSync('static/js/app_policy.js','utf8'),{context});
await policy.link(()=>{throw new Error('Unexpected policy dependency');});
await policy.evaluate();
const mod=new vm.SourceTextModule(readFileSync('static/js/chat_payloads.js','utf8'),{context});
await mod.link(specifier=>{
 if(specifier==='./app_policy.js')return policy;
 const values=specifier==='./state.js'?{state}:{isToolEnabled:()=>true,isToolAutoApprove:()=>false};
 return new vm.SyntheticModule(Object.keys(values),function(){
  for(const [key,value] of Object.entries(values))this.setExport(key,value);
 },{context});
});
await mod.evaluate();
const build=mod.namespace.buildApiMessages;
const history=[{role:'user',content:'Read this',attachments:[{name:'report.pdf',path:'/workspace/uploads/report.pdf'}]}];
let messages=await build(history);
assert(messages[0].content.startsWith('Global instructions\n\n'));
assert(!messages[0].content.includes('Folder instructions'));
assert(!messages[0].content.includes('Folder/Project'));
assert(!messages[0].content.includes('/workspace/uploads/'));
assert.equal(messages[1].content,'Read this\n\nAttached file(s) available in the chat workspace:\n- report.pdf: /workspace/uploads/report.pdf');
assert.equal(history[0].content,'Read this','Payload assembly must not mutate saved history');
state.folderId='folder-a';
messages=await build(history);
assert(messages[0].content.startsWith('Folder instructions\n\n'));
assert(messages[0].content.includes('shares its workspace and container'));
assert(!messages[0].content.includes('Global instructions'));
state.folderSystemPrompt=' ';
assert((await build(history))[0].content.startsWith('Global instructions\n\n'));
// Explicit turn snapshots must win over whichever chat the user navigates to.
state.folderId='folder-b';
state.folderSystemPrompt='Other folder instructions';
messages=await build(history,'Captured standalone instructions',null);
assert(messages[0].content.startsWith('Captured standalone instructions\n\n'));
assert(!messages[0].content.includes('Folder/Project'));
state.folderId=null;
messages=await build(history,'Captured folder instructions','folder-a');
assert(messages[0].content.startsWith('Captured folder instructions\n\n'));
assert(messages[0].content.includes('shares its workspace and container'));
''')


def test_sse_handles_fragmented_unicode_truncation_and_callback_errors():
    run_node(r'''
import assert from 'node:assert/strict';
import {readSSEStream} from './static/js/stream_consumer.js';
const bytes = new TextEncoder().encode(': heartbeat\n\ndata: broken\n\ndata: {"type":"text","content":"🌍"}\n\ndata: [DONE]\n\n');
const body = new ReadableStream({start(controller) {
  for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
  controller.close();
}});
const seen = [];
assert(await readSSEStream({body}, event => {seen.push(event); return true;}));
assert.deepEqual(seen, [{type:'text',content:'🌍'}]);
assert(!body.locked);
const response = text => ({body: new ReadableStream({start(controller) {
  controller.enqueue(new TextEncoder().encode(text)); controller.close();
}})});
await assert.rejects(readSSEStream(response('data: {}\n\n'), () => true), /before completion/);
const failed = response('data: {}\n\ndata: [DONE]\n\n');
await assert.rejects(readSSEStream(failed, () => {throw new Error('Handler failed');}), /Handler failed/);
assert(!failed.body.locked);
''')


def test_conversation_activity_survives_navigation_and_clears_background_turns():
    run_node(r'''
import assert from 'node:assert/strict';
import {api} from './static/js/api.js';
import * as activity from './static/js/conversation_activity.js';
const {activeTurns, updateConversationActivity:update, syncConversationActivity:sync, workingTitleAttributes} = activity;
let writes = 0;
const indicator = ids => ({
  dataset:{workingConversations:ids}, 'aria-busy':'false',
  getAttribute(name){return this[name];},
  setAttribute(name,value){this[name]=value; writes++;},
});
const isWorking = el => el['aria-busy'] === 'true';
const a = indicator('a'), b = indicator('b'), folder = indicator('a b');
let indicators = [a, b, folder];
globalThis.CSS = {escape:s=>s};
globalThis.document = {querySelectorAll:selector => {
  const ids = [...selector.matchAll(/~="([^"]+)"/g)].map(match=>match[1]);
  return indicators.filter(el=>el.dataset.workingConversations.split(' ').some(id=>ids.includes(id)));
}};
const timers = [];
globalThis.setTimeout = callback => {timers.push(callback); return 1;};
const start = id => {activeTurns.set(id, {}); update(id);};
const finish = (id, disconnected=false) => {activeTurns.delete(id); update(id, disconnected);};
const initialVersion = activity.activityVersion;
start('a');
assert(isWorking(a));
assert(!isWorking(b));
assert(isWorking(folder));
assert(workingTitleAttributes('a').includes('aria-busy="true"'));
const rebuiltA = indicator('a');
indicators = [rebuiltA, b, folder];
sync([{id:'a'}], initialVersion);
assert(workingTitleAttributes('a').includes('aria-busy="true"'));
start('b');
const staleVersion = activity.activityVersion;
finish('a');
assert(!isWorking(rebuiltA));
assert(isWorking(b), 'Background completion must not clear another running chat');
assert(isWorking(folder));
sync([{id:'a',active_stream_id:'old-stream'}], staleVersion);
assert(!isWorking(rebuiltA), 'A stale response cannot revive a completed turn');
finish('b');
for (let i=0; i<1000; i++) {start(`chat-${i}`); finish(`chat-${i}`);}
assert.equal(activeTurns.size, 0);
assert.equal(timers.length, 0, 'Local streams never need status polling');
assert(!isWorking(folder));

// Server-only turns poll compact status; attaching a consumer stops requests.
sync([{id:'a',active_stream_id:'server-stream'}]);
assert(isWorking(rebuiltA));
start('a');
sync([{id:'a',active_stream_id:'server-stream'}]);
api.get = async () => {throw new Error('Local consumer should prevent polling');};
await timers.shift()();
assert.equal(timers.length, 0);

// A disconnected consumer hands off to polling instead of reporting completion.
finish('a', true);
assert(isWorking(rebuiltA));
api.get = async url => {
  assert.equal(url, '/api/conversations/activity');
  return [{id:'a',active_stream_id:'server-stream'}];
};
const writesBeforePoll = writes;
await timers.shift()();
assert(isWorking(rebuiltA));
assert.equal(writes, writesBeforePoll, 'Unchanged activity must not rewrite DOM attributes');
api.get = async () => {throw new Error('Temporary network failure');};
await timers.shift()();
assert(isWorking(rebuiltA));
assert.equal(timers.length, 1);
api.get = async () => [];
await timers.shift()();
assert(!isWorking(rebuiltA));
assert(!isWorking(folder));
assert.equal(timers.length, 0);

// Completion while a poll is in flight invalidates that older response.
sync([{id:'a',active_stream_id:'server-stream'}]);
let resolvePoll;
api.get = () => new Promise(resolve=>resolvePoll=resolve);
const polling = timers.shift()();
start('a'); finish('a');
resolvePoll([{id:'a',active_stream_id:'server-stream'}]);
await polling;
assert(!isWorking(rebuiltA));
assert.equal(timers.length, 0);
assert(workingTitleAttributes('a').includes('aria-busy="false"'));
assert.equal(rebuiltA['aria-busy'], 'false');
''')


def test_chat_approvals_survive_navigation_without_blocking_or_cross_chat_cancellation():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const state = {convId:'a',isStreaming:true,streamId:'stream-a'};
const posts=[], approvals=[], results=[], running=[], activityChanges=[], markdown=[], workLogChanges=[];
const frames=new Map(); let nextFrame=0;
const noop = () => {};
const renderer = Object.fromEntries([
  'appendAssistantStatus','finalizeStreamingMessage','setStreamingMessageLogIndex',
  'scrollToBottom','renderAllMessages','refreshMessageFooter','createThinkingBlock',
  'updateThinkingBlock','finalizeThinkingBlock',
].map(name => [name, noop]));
Object.assign(renderer, {
  createStreamingMessage: () => ({}),
  createToolStrip: name => ({name}),
  toolStripSetRunning: strip => running.push(strip),
  toolStripFinalize: strip => results.push(strip),
  toolStripSetApproval: (strip,name,args,decide) => {
    const approval={strip,name,args,decide,disposed:false};
    approvals.push(approval);
    return () => approval.disposed=true;
  },
});
const modules = {
  './api.js': {api:{post:async (url,body) => {posts.push({url,body});}}},
  './state.js': {state},
  './renderer.js': renderer,
  './renderer_groups.js': {setWorkLogProcessing:active=>workLogChanges.push(active)},
  './markdown.js': {applyMarkdown:(el,text)=>markdown.push(text)},
  './conversations.js': {persistConversationFor:noop,createNewConversation:noop},
  './file_panel.js': {refreshFilePanel:async () => {}},
  './format.js': {escapeHtml:value=>value},
  './stream_consumer.js': {readResponseError:noop,readSSEStream:async()=>true},
  './chat_attachments.js': {initImageAttachments:noop,hasPendingAttachments:noop},
  './chat_payloads.js': {buildApiMessages:noop,buildToolsPayload:noop,buildMcpToolMetaPayload:noop},
  './ids.js': {createClientId:noop},
  './chat_send.js': {sendMessage:noop},
  './chat_edit.js': {editAndResend:noop,regenerateFrom:noop},
  './chat_log_utils.js': {lastAssistantMessageIndex:()=>-1,isTransientStatus:entry=>entry.presentation==='toast'},
  './ui.js': {showToast:noop},
  './conversation_activity.js': {
    activeTurns:new Map(),
    updateConversationActivity:(id,disconnected=false)=>activityChanges.push([id,disconnected]),
  },
};
const context=vm.createContext({document:{addEventListener:noop,getElementById:()=>null},AbortController,
 requestAnimationFrame:callback=>{frames.set(++nextFrame,callback);return nextFrame;},
 cancelAnimationFrame:id=>frames.delete(id),
});
const source=readFileSync('static/js/chat.js','utf8')+
  '\nexport {createStreamContext,processSSEEvent,reattachRuntime,activeTurns,attachServerStream,releaseTurnUI};';
const module=new vm.SourceTextModule(source,{context});
const linked=new Map();
await module.link(specifier=>{
  if (!linked.has(specifier)) {
    const exports=modules[specifier];
    assert(exports, specifier);
    linked.set(specifier,new vm.SyntheticModule(Object.keys(exports),function(){
      for(const [name,value] of Object.entries(exports)) this.setExport(name,value);
    },{context}));
  }
  return linked.get(specifier);
});
await module.evaluate();
const {createStreamContext,processSSEEvent:apply,reattachRuntime,activeTurns,stopAssistantTurn}=module.namespace;
const turn={convId:'a',messages:[],displayLog:[]};
const ctx=createStreamContext(turn,'stream-a');
await apply({type:'text',content:'First'},ctx);
await apply({type:'text',content:' second'},ctx);
assert.equal(frames.size,1,'Token bursts render Markdown only once per animation frame');
assert.equal(markdown.length,0);
const paint=frames.values().next().value;frames.clear();paint();
assert.deepEqual(markdown,['First second']);
await apply({type:'text',content:' third'},ctx);
assert.equal(frames.size,1);
state.convId='b';
module.namespace.releaseTurnUI(ctx);
assert.equal(frames.size,0,'Navigation cancels queued Markdown work and releases old DOM');
assert.equal(ctx.contentEl,null);
await apply({type:'text',content:' background'},ctx);
assert.equal(frames.size,0,'Background tokens must not build detached DOM');
state.convId='a';
reattachRuntime({turn,ctx});
assert.equal(markdown.at(-1),'First second third background');
assert.equal(workLogChanges.at(-1),true,'Reattaching a live turn restores its work-log status');
await apply({type:'text',content:' final'},ctx);
await apply({type:'tool_start',name:'view'},ctx);
await apply({type:'tool_start',name:'external'},ctx);
await apply({type:'tool_running',name:'view'},ctx);
await apply({type:'tool_result',name:'view',result:'file'},ctx);
assert.equal(frames.size,0,'Tool boundaries cancel stale partial Markdown renders');
await apply({type:'tool_approval_required',name:'external',call_id:'one',args:{}},ctx);
assert.equal(approvals.length,1,'Approval must not block event consumption');
assert.equal(approvals[0].strip,ctx.toolStrips[1],'Mixed auto/manual tools must use the matching strip');
await apply({type:'tool_result',name:'external',result:'Timed out'},ctx);
assert(approvals[0].disposed);
assert.equal(results[1],ctx.toolStrips[1]);

// An approval arriving in a background chat stays pending until it is visible.
state.convId='b';
await apply({type:'tool_start',name:'external'},ctx);
await apply({type:'tool_approval_required',name:'external',call_id:'two',args:{x:1}},ctx);
assert.equal(posts.length,0,'Navigation must not deny a tool call');
state.convId='a';
const runtime={turn,ctx,streamId:'stream-a'};
activeTurns.set('a',runtime);
reattachRuntime(runtime);
assert.equal(approvals.length,2);
assert.equal(approvals[1].name,'external');

let otherDisposed=false;
activeTurns.set('b',{ctx:{disposeApproval:()=>otherDisposed=true}});
await stopAssistantTurn();
assert(approvals[1].disposed);
assert(!otherDisposed,'Stopping one chat must not deny another chat approvals');
assert.equal(posts.at(-1).url,'/api/chat/cancel');
assert.equal(posts.at(-1).body.stream_id,'stream-a');
assert(state.isStreaming,'Composer waits for authoritative completion');
await apply({type:'assistant_done',messages:[],displayLog:[]},ctx);
assert(ctx.assistantDone);
assert.equal(workLogChanges.at(-1),false,'Authoritative completion finalizes visible work logs');
const visibleStatusCount=workLogChanges.length;

// The real attachment lifecycle releases local history, but distinguishes a
// terminal stream from a disconnect while the independent worker is running.
activeTurns.delete('a');
state.convId='b';
modules['./api.js'].api.stream=async()=>({ok:true});
assert(await module.namespace.attachServerStream('a','success',{title:'Working'}));
assert.deepEqual(activityChanges.slice(-2),[['a',false],['a',false]]);
assert(!activeTurns.has('a'));
modules['./api.js'].api.stream=async()=>{throw new Error('Disconnected');};
assert(!await module.namespace.attachServerStream('a','disconnected',{title:'Working'}));
assert.deepEqual(activityChanges.slice(-2),[['a',false],['a',true]]);
assert(!activeTurns.has('a'),'Disconnected consumers must release their retained history');
assert(activeTurns.has('b'),'Finishing a background consumer must preserve other turns');
assert.equal(workLogChanges.length,visibleStatusCount,'Background completion must not change the visible work log');
''')


def test_shared_source_preserves_lines_escapes_and_bounds_large_previews():
    run_node(r'''
import assert from 'node:assert/strict';
import {renderSource} from './static/js/source_view.js';
import view from './static/js/native_tools/view.js';
const raw = '<script>\r\n\tvalue & "text"\r\n\r\n';
const numbered = '1: <script>\n2: \tvalue & "text"\n3: \n';
assert.equal(renderSource(raw), renderSource(numbered, {numbered: true}));
assert(!renderSource(raw).includes('<script>'));
assert(renderSource(raw).includes('&lt;script&gt;'));
assert.equal((renderSource('').match(/class="source-line"/g) || []).length, 1);
const range = view.renderResult('105: <img>\n106: \n[more content]');
assert(range.includes('>105</span>'));
assert(range.includes('&lt;img&gt;'));
assert(range.includes('[more content]'));
const lines = Array.from({length: 300}, (_, i) => `${i + 1}: line ${i + 1}`).join('\n');
assert(view.renderResult(lines).includes('line 300'), 'View must not silently discard lines after 250');
const large = renderSource('row\n'.repeat(100000));
assert.equal((large.match(/class="source-line"/g) || []).length, 2000);
assert(large.includes('Preview shortened'));
assert(renderSource('x'.repeat(200000)).includes('Preview shortened'));
let highlights = 0;
globalThis.hljs = {getLanguage: () => true, highlight: () => { highlights++; return {value: 'highlighted'}; }};
renderSource('row\n'.repeat(500), {filename:'large.py'});
assert.equal(highlights, 0, 'Large files should skip per-line syntax parsing');
renderSource('print(1)', {filename:'small.py'});
assert.equal(highlights, 1);
''')


def test_chat_navigation_keeps_latest_selection_and_new_chat_cancels_pending_open():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const pending=[];
const state={convId:null};
const rendered=[];
const elements=Object.fromEntries(['chat-title-input','chat-folder-prefix','chat-title-separator','conv-list'].map(id => [id,{
  value:'',setAttribute(){},addEventListener(){},classList:{remove(){}},
}]));
globalThis.document={
  addEventListener(){}, dispatchEvent(){}, getElementById:id=>elements[id],
  querySelector:()=>null, querySelectorAll:()=>[],
};
globalThis.CSS={escape:s=>s};
globalThis.CustomEvent=class {};
globalThis.window={innerWidth:1200};
const dependencies={
  './api.js':{api:{get:()=>new Promise(resolve=>pending.push(resolve))}},
  './state.js':{state,STORAGE_KEYS:{}}, './storage.js':{storage:{set(){},remove(){}}},
  './renderer.js':{clearMessages:()=>rendered.push('empty'),renderAllMessages:log=>rendered.push(log[0].content)},
  './ui.js':{toggleSidebar(){},openModal(){},closeModal(){},showToast(){}}, './icons.js':{ICONS:{}},
  './format.js':{escapeHtml:s=>s,formatConversationDate:()=>''}, './file_panel.js':{refreshFilePanel(){},resetFilePanel(){}},
  './conversation_activity.js':{activityVersion:0,workingTitleAttributes:()=>'',syncConversationActivity(){}},
};
const module=new vm.SourceTextModule(readFileSync('static/js/conversations.js','utf8'));
await module.link(specifier=>new vm.SyntheticModule(Object.keys(dependencies[specifier]),function(){
  for(const [key,value] of Object.entries(dependencies[specifier])) this.setExport(key,value);
}));
await module.evaluate();
const {openConversation,startNewChat}=module.namespace;
const first=openConversation('first');
const second=openConversation('second');
pending[1]({title:'Second',displayLog:[{content:'second content'}]});
await second;
pending[0]({title:'First',displayLog:[{content:'first content'}]});
await first;
assert.equal(state.convId,'second');
assert.deepEqual(rendered,['second content']);
const old=openConversation('old');
startNewChat();
pending[2]({title:'Old',displayLog:[{content:'old content'}]});
await old;
assert.equal(state.convId,null);
assert.deepEqual(rendered,['second content','empty']);
let finishCreate;
dependencies['./api.js'].api.post=()=>new Promise(resolve=>finishCreate=resolve);
const creating=module.namespace.createNewConversation();
startNewChat();
finishCreate({id:'created-in-background'});
await Promise.resolve();await Promise.resolve();
pending[3]([]);pending[4]([]);
const created=await creating;
assert.equal(created.id,'created-in-background');
assert.equal(state.convId,null,'A delayed new-chat response must not override a newer selection');
''')


def test_image_send_creates_owner_before_upload_and_restores_failed_uploads():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const state={convId:null,isStreaming:false};
const entry={kind:'image',previewUrl:'data:image/png;base64,YWJj',mediaType:'image/png',name:'image.png',size:3};
let pending=[entry]; const order=[]; let failUpload=true; let saved;
const deps={
 hasPendingAttachments:()=>pending.length>0,
 setStreaming:value=>state.isStreaming=value,
 createTurnContext:convId=>({convId,messages:[],displayLog:[]}),
 createNewConversation:async()=>{order.push('create');state.convId='owner';return {id:'owner'};},
 syncVisibleTurn(){},isTurnVisible:turn=>turn.convId===state.convId,
 persistTurnConversation:async turn=>saved=structuredClone(turn),
 runAssistantTurnAndPersist:async()=>{order.push('assistant');state.isStreaming=false;},
};
const dependencies={
 './state.js':{state},'./renderer.js':{appendMessage(){}},'./chat_branches.js':{syncVisibleBranches(){}},
 './file_panel.js':{refreshFilePanel:async()=>{}},
 './chat_attachments.js':{
  waitForPendingAttachmentAdds:async()=>{},refreshImagePreviewBar(){},
  drainPendingAttachments:()=>pending.splice(0),restorePendingAttachments:entries=>pending.unshift(...entries),
  uploadImage:async(owner,dataUrl,mime)=>{
   assert.equal(owner,'owner');assert.equal(dataUrl,'data:image/png;base64,YWJj');assert.equal(mime,'image/png');
   order.push('image');return failUpload?null:{ref:'image.png',url:'/api/conversations/owner/images/image.png',mediaType:mime};
  },
  uploadConversationFiles:async(owner,entries)=>{
   assert.equal(owner,'owner');assert.equal(entries[0].kind,'image');order.push('workspace');
   return [{name:'image.png',path:'/workspace/uploads/image.png',size:3}];
  },
 },
};
const module=new vm.SourceTextModule(readFileSync('static/js/chat_send.js','utf8'));
await module.link(specifier=>new vm.SyntheticModule(Object.keys(dependencies[specifier]),function(){
 for(const [key,value] of Object.entries(dependencies[specifier]))this.setExport(key,value);
}));
await module.evaluate();
await module.namespace.sendMessage('Image',deps);
assert.deepEqual(order,['create','image']);
assert.deepEqual(pending,[entry]);
assert.equal(state.isStreaming,false);
failUpload=false;
await module.namespace.sendMessage('Image',deps);
assert.deepEqual(order,['create','image','image','workspace','assistant']);
assert.equal(pending.length,0);
assert.equal(saved.messages[0].content[1].ref,'image.png');
assert.equal(saved.messages[0].attachments[0].path,'/workspace/uploads/image.png');
assert.equal(saved.displayLog[0].content.attachments[0].url,'/api/conversations/owner/images/image.png');
assert.equal(entry.previewUrl,'/api/conversations/owner/images/image.png','Release the local base64 after upload');
''')


def test_failed_authoritative_history_reload_blocks_saves_until_recovered():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const handlers=new Map(), writes=[];
const noop=()=>{};
const state={convId:'other',folderSystemPrompt:'',systemPrompt:''};
let response={error:'Temporary server error'};
const overrides={
 './api.js':{api:{get:async()=>{if(response instanceof Error)throw response;return response;}}},
 './state.js':{state},
 './conversations.js':{persistConversationFor:async(id,data)=>writes.push({id,data})},
 './conversation_activity.js':{activeTurns:new Map()},
};
const context=vm.createContext({
 document:{addEventListener:(name,handler)=>handlers.set(name,handler),getElementById:()=>null},
 console,AbortController,
});
const source=readFileSync('static/js/chat.js','utf8');
const names=new Map();
for(const match of source.matchAll(/(?:import|export)\s*\{([^}]+)\}\s*from\s*'([^']+)'/gs)){
 const exports=names.get(match[2])||new Set();
 for(const name of match[1].split(','))exports.add(name.trim().split(/\s+as\s+/)[0]);
 names.set(match[2],exports);
}
const module=new vm.SourceTextModule(source+'\nexport {processSSEEvent,createStreamContext,createTurnContext,persistTurnConversation};',{context});
const linked=new Map();
await module.link(specifier=>{
 if(!linked.has(specifier))linked.set(specifier,new vm.SyntheticModule([...names.get(specifier)],function(){
  for(const name of names.get(specifier))this.setExport(name,overrides[specifier]?.[name]??noop);
 },{context}));
 return linked.get(specifier);
});
await module.evaluate();
const {processSSEEvent:apply,createStreamContext,createTurnContext,persistTurnConversation}=module.namespace;
const turn={convId:'review',messages:[{role:'user',content:'Old history'}],displayLog:[]};
const ctx=createStreamContext(turn,'stream');
for(const failure of [{error:'Temporary server error'},{messages:[]},new Error('Offline')]){
 response=failure;
 await assert.rejects(apply({type:'history_reload',conv_id:'review'},ctx));
 assert.equal(ctx.assistantDone,false);
 await assert.rejects(persistTurnConversation(turn),/History recovery is incomplete/);
 assert.throws(()=>createTurnContext('review'),/History recovery is incomplete/);
}
assert.equal(writes.length,0,'Failed reload must never overwrite completed server history');
response={messages:[{role:'assistant',content:'Completed server answer'}],displayLog:[{type:'message',role:'assistant',content:'Completed server answer'}]};
await apply({type:'history_reload',conv_id:'review'},ctx);
assert.equal(ctx.assistantDone,true);
await persistTurnConversation(turn);
assert.deepEqual(writes[0].data.messages,response.messages);
// Reopening with successfully fetched authoritative history clears the shared guard,
// but never authorizes an older in-flight turn to save its partial arrays.
response={error:'Temporary server error'};
await assert.rejects(apply({type:'history_reload',conv_id:'review'},ctx));
const reopened={messages:[{role:'assistant',content:'Recovered on reopening'}],displayLog:[]};
await handlers.get('chat:conversation-opened')({detail:{convId:'review',data:reopened}});
await assert.rejects(persistTurnConversation(turn),/History recovery is incomplete/,
 'A stale in-flight turn stays blocked even if another view reopens the chat');
await persistTurnConversation({convId:'review',...reopened});
assert.equal(writes.length,2);
''')


def test_stacked_activity_preserves_independent_disclosures_during_updates():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
class Element {
 constructor(classes=[]) { this.classes=new Set(classes); this.dataset={}; this.attrs={}; this.events={}; this.children=[]; this.textContent=''; this.style={}; this.classList={contains:c=>this.classes.has(c),toggle:(c,on)=>{const enabled=on??!this.classes.has(c); enabled?this.classes.add(c):this.classes.delete(c); return enabled;}}; }
 setAttribute(k,v){this.attrs[k]=v;}
 addEventListener(k,fn){this.events[k]=fn;}
}
const makeBlock=(classes,label)=>{
 const block=new Element(classes),header=new Element(),body=new Element(),chevron=new Element();
 block.querySelector=s=>({'.header':header,'.body':body,'.chevron':chevron,'.thinking-label':{textContent:label},'.tr-tool-name':{textContent:label}})[s];
 return {block,header,body};
};
const thought=makeBlock(['thinking-block'],'Thought process');
const tool=makeBlock(['tool-strip','tool-strip-running'],'Bash · echo hello');
tool.block.dataset={toolName:'bash',displayName:'Bash · echo hello'};
const body=new Element();body.children=[thought.block,tool.block];
const selectors=['.group-label','.group-emblem'];
const nodes=Object.fromEntries(selectors.map(s=>[s,new Element()]));nodes['.group-body']=body;
const group=new Element();group.querySelector=s=>nodes[s];
const messages={children:[]};
const row=new Element(['msg-row']);row.querySelectorAll=()=>[group];messages.children=[row];
const context=vm.createContext({});
const mod=new vm.SourceTextModule(readFileSync('static/js/renderer_groups.js','utf8'),{context});
const deps={
 './dom.js':{createElement:()=>new Element(),setVisible:(el,on)=>{el.style.display=on?'block':'none';}},
 './icons.js':{ICONS:{chevronRight:'right',layers:'layers',bulb:'bulb'}},'./state.js':{state:{}},
 './renderer_core.js':{messagesEl:()=>messages,createMessageRow:()=>null}
};
await mod.link(name=>new vm.SyntheticModule(Object.keys(deps[name]),function(){for(const [key,value] of Object.entries(deps[name]))this.setExport(key,value);},{context}));
await mod.evaluate();
const {updateGroupLabel:update,attachCollapsible,setWorkLogProcessing}=mod.namespace;
for(const {block} of [thought,tool]) attachCollapsible(block,{headerSelector:'.header',bodySelector:'.body',chevronSelector:'.chevron'});
thought.header.events.click();tool.header.events.click();update(group);
assert.equal(thought.body.style.display,'block');assert.equal(tool.body.style.display,'block');
assert.equal(thought.header.attrs['aria-expanded'],'true');assert.equal(tool.header.attrs['aria-expanded'],'true');
assert.equal(nodes['.group-label'].textContent,'Bash · echo hello');
tool.block.classList.toggle('tool-strip-running',false);update(group);
assert.equal(nodes['.group-label'].textContent,'Work log');
assert.equal(thought.block.classList.contains('open'),true);assert.equal(tool.block.classList.contains('open'),true);
// Closing one step must leave the other step open.
thought.header.events.click();update(group);
assert.equal(thought.body.style.display,'none');assert.equal(tool.body.style.display,'block');
assert.equal(thought.header.attrs['aria-expanded'],'false');
// Removing an approval step does not change its neighbors.
body.children=[tool.block];update(group);
assert.equal(tool.block.classList.contains('open'),true);
// Processing stays visible even before any thinking or tool events arrive.
body.children=[];setWorkLogProcessing(true);
assert.equal(nodes['.group-label'].textContent,'Working');assert.equal(group.hidden,false);
body.children=[thought.block];thought.block.classList.toggle('thinking-streaming',true);update(group);
assert.equal(nodes['.group-label'].textContent,'Thinking');assert(nodes['.group-emblem'].innerHTML.includes('group-progress'));
body.children.push(tool.block);tool.block.classList.toggle('tool-strip-running',true);update(group);
assert.equal(nodes['.group-label'].textContent,'Bash · echo hello');
tool.block.querySelector=s=>s==='.thinking-icon, .tool-icon'?{innerHTML:'bash-icon'}:null;update(group);
assert(nodes['.group-emblem'].innerHTML.includes('group-progress'));
assert(!nodes['.group-emblem'].innerHTML.includes('bash-icon'));
thought.block.classList.toggle('thinking-streaming',false);tool.block.classList.toggle('tool-strip-running',false);update(group);
assert.equal(nodes['.group-label'].textContent,'Working');assert(nodes['.group-emblem'].innerHTML.includes('group-progress'));
// Finishing (including stop/error cleanup) overrides any stale streaming classes.
tool.block.classList.toggle('tool-strip-running',true);setWorkLogProcessing(false);
assert.equal(nodes['.group-label'].textContent,'Work log');assert.equal(nodes['.group-emblem'].innerHTML,'layers');
body.children=[];update(group);assert.equal(group.hidden,true);
''')


def test_work_log_collapse_setting_migrates_and_updates_existing_groups():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
let saved={},written;
const toggle={checked:false};
const log={open:false,header:{setAttribute(k,v){this[k]=v;}},workspace:{hidden:true}};
log.classList={toggle:(_name,on)=>{log.open=on;}};
log.querySelector=s=>s==='.group-header'?log.header:log.workspace;
const root={style:{setProperty(){}},classList:{toggle(){}},setAttribute(){},getAttribute(){return 'dark';}};
const context=vm.createContext({
 document:{documentElement:root,getElementById:id=>id==='cust-collapse-seq-blocks'?toggle:null,querySelector:()=>null,querySelectorAll:s=>s==='.block-group'?[log]:[]},
 window:{innerWidth:1200,matchMedia:()=>({matches:false,addEventListener(){}})}
});
const stateModule=new vm.SourceTextModule(readFileSync('static/js/state.js','utf8'),{context});
await stateModule.link(()=>{throw Error('Unexpected state dependency');});await stateModule.evaluate();
const storageModule=new vm.SyntheticModule(['storage'],function(){this.setExport('storage',{get:()=>saved,set:(_key,value)=>{written=value;}});},{context});
const mod=new vm.SourceTextModule(readFileSync('static/js/customization.js','utf8'),{context});
await mod.link(name=>name==='./state.js'?stateModule:storageModule);await mod.evaluate();
const {state}=stateModule.namespace;
const {loadCustomization:load,saveCustomization:save}=mod.namespace;
load();assert.equal(state.collapseSequentialBlocks,true);assert.equal(toggle.checked,true);
for(const collapsed of [true,false]) {
 saved={groupSequentialBlocks:collapsed,hideToolBlocks:true};load();
 assert.equal(state.collapseSequentialBlocks,collapsed);assert.equal(toggle.checked,collapsed);
}
// A saved preference with the new name wins over the legacy field.
saved={collapseSequentialBlocks:false,groupSequentialBlocks:true,hideToolBlocks:true};load();
assert.equal(toggle.checked,false);
toggle.checked=true;save();
assert.equal(state.collapseSequentialBlocks,true);assert.equal(written.collapseSequentialBlocks,true);
assert.equal(written.groupSequentialBlocks,undefined);assert.equal(written.hideToolBlocks,true);
assert.equal(log.open,false);assert.equal(log.workspace.hidden,true);assert.equal(log.header['aria-expanded'],'false');
toggle.checked=false;save();
assert.equal(log.open,true);assert.equal(log.workspace.hidden,false);assert.equal(log.header['aria-expanded'],'true');
// Applying an unrelated preference preserves a manually closed work log.
log.open=false;log.workspace.hidden=true;save();assert.equal(log.open,false);assert.equal(log.workspace.hidden,true);
saved=written;load();assert.equal(toggle.checked,false);

''')


def test_sequential_blocks_always_group_and_collapse_is_independent_of_tool_details():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const state={};
function element(classes=[]) {
 const names=new Set(classes);
 return {children:[],dataset:{},events:{},attrs:{},classList:{contains:c=>names.has(c),toggle(c,on){const enabled=on??!names.has(c);enabled?names.add(c):names.delete(c);return enabled;}},
 setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){this.events[k]=fn;},appendChild(el){this.children.push(el);el.parentElement=this;}};
}
const context=vm.createContext({});
const mod=new vm.SourceTextModule(readFileSync('static/js/renderer_groups.js','utf8'),{context});
const deps={
 './dom.js':{setVisible(){},createElement:(_tag,options)=>{
  const group=element(options.className.split(' '));
  const nodes=Object.fromEntries(['.group-header','.activity-workspace','.group-body','.group-label'].map(s=>[s,element()]));
  group.querySelector=s=>nodes[s];return group;
 }},'./state.js':{state},'./icons.js':{ICONS:{layers:'',chevronRight:''}},
 './renderer_core.js':{messagesEl:()=>null,createMessageRow:()=>null}
};
await mod.link(name=>new vm.SyntheticModule(Object.keys(deps[name]),function(){for(const [key,value] of Object.entries(deps[name]))this.setExport(key,value);},{context}));await mod.evaluate();
for(const collapsed of [false,true])for(const hideDetails of [false,true]) {
 Object.assign(state,{hideToolBlocks:hideDetails,collapseSequentialBlocks:collapsed});
 const row=element();row.insertBefore=group=>{row.group=group;};
 const block=element(['thinking-block']);block.querySelector=()=>({textContent:'Thought process'});block.parentElement=row;
 mod.namespace.tryGroupBlock(block);
 assert(row.group);
 assert.equal(row.group.classList.contains('open'),!collapsed);
 assert.equal(row.group.querySelector('.group-body').children[0],block);
 assert(row.group.innerHTML.includes(`aria-expanded="${!collapsed}"`));
 row.group.querySelector('.group-header').events.click();
 assert.equal(row.group.classList.contains('open'),collapsed);
 assert.equal(row.group.querySelector('.activity-workspace').hidden,!collapsed);
}
''')
