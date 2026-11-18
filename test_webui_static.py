"""Exercise the bare frontend with a small DOM/API double, without a WebView."""

from pathlib import Path
import shutil
import subprocess
import unittest


HARNESS = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag='div', type=1) { this.tagName=tag; this.nodeType=type; this.value = ''; this._text = ''; this.children = []; this.listeners = {}; this.attributes={}; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(text) { this._text=String(text); this.children=[]; }
  append(...children) { for (const child of children) {if (child.nodeType === 11) {this.children.push(...child.children); child.children=[];} else this.children.push(child);} }
  replaceChildren(...children) { this._text=''; this.children=[]; this.append(...children); }
  setAttribute(name,value) { this.attributes[name]=value; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
}
const elements = new Map();
const document = {
  getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
  createElement(tag) { return new Element(tag); },
  createTextNode(text) {const node=new Element('#text',3); node.textContent=text; return node;},
  createDocumentFragment() {return new Element('#fragment',11);}
};
const chat = {id:'chat-a', title:'Chat A', model:'fake:7b', messages:[]};
const saved = { ...chat, messages:[] };
const calls = [];
const window = {listeners:{}, addEventListener(type, callback) {this.listeners[type] = callback;}};
window.pywebview = {api: {
  state: async () => ({ok:true, engine:'Ollama', host:'localhost', models:['fake:7b']}),
  list_chats: async () => ({ok:true, chats:[saved]}),
  open_chat: async () => ({ok:true, chat:structuredClone(saved)}),
  send: async (...args) => { calls.push(args); saved.messages.push({role:'user',content:args[1]}); return {ok:true,stream_id:'stream-a'}; },
  stop: async id => {calls.push(['stop',id]); return {ok:true};}
}};
vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'markdown.js'), 'utf8'), {document, window});
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {document, window, Map, Error, String, Number, Boolean, Array});
(async () => {
  await window.listeners.pywebviewready();
  elements.get('model').value = 'fake:7b';
  const hostile = '<img src=x onerror=alert(1)>';
  elements.get('prompt').value = 'Hi';
  await elements.get('composer').listeners.submit({preventDefault(){}});
  assert.equal(calls[0][0], 'chat-a');
  window.app.onEvent([{type:'token',chat_id:'chat-a',stream_id:'stream-a',text:hostile}]);
  assert.equal(elements.get('messages').children.at(-1).textContent, hostile);
  assert.equal(elements.get('stop').disabled, false);
  await elements.get('stop').listeners.click();
  assert.deepEqual(calls[1], ['stop','stream-a']);
  saved.messages.push({role:'assistant',content:hostile});
  window.app.onEvent([{type:'stopped',chat_id:'chat-a',stream_id:'stream-a',text:hostile,metrics:{}}]);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(elements.get('stop').disabled, true);
  assert.equal(elements.get('messages').children.at(-1).children.at(-1).textContent, hostile);
  process.stdout.write('Frontend model/send/token/stop checks passed\n');
})().catch(error => { console.error(error); process.exitCode=1; });
'''


class StaticTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_compaction_controls_collapse_archive_and_create_empty_environment_chat(self):
        self.run_scenario(r'''
        saved.messages = Array.from({length:6}, (_,i)=>({role:i%2?'assistant':'user',content:'original-'+i}));
        const records = new Map([['chat-a',saved]]);
        window.pywebview.api.open_chat = async id => ({ok:true,chat:structuredClone(records.get(id))});
        window.pywebview.api.context_usage = async () => ({ok:true,used:20,total:8192,level:'ok'});
        window.pywebview.api.compact = async id => {
          window.app.onEvent([{type:'compact_started',operation:'compact',stream_id:'c1',chat_id:id}]);
          saved.summary={text:'kept decisions',upto:2,created_at:'2026-11-18T00:00:00Z'};
          window.app.onEvent([{type:'compact_done',operation:'compact',stream_id:'c1',chat_id:id}]);
          return {ok:true,stream_id:'c1'};
        };
        window.pywebview.api.new_chat_same_environment = async id => {
          calls.push(['copy-env',id]);
          const fresh={id:'chat-b',title:'New empty chat',model:'fake:7b',messages:[],summary:null};
          records.set(fresh.id,fresh);
          return {ok:true,chat:fresh};
        };
        await window.listeners.pywebviewready();
        await elements.get('compact').listeners.click();
        await new Promise(resolve=>setImmediate(resolve));
        const archive=elements.get('messages').children[0];
        assert.equal(archive.tagName,'details');
        assert.match(archive.textContent,/original-0/);
        assert.match(archive.textContent,/original-1/);
        assert.equal(elements.get('stop').disabled,true);
        await elements.get('same-environment').listeners.click();
        assert.equal(elements.get('chat-title').textContent,'New empty chat');
        assert.equal(elements.get('messages').children.length,0);
        assert.deepEqual(calls.at(-1),['copy-env','chat-a']);
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_saved_history_stays_accessible_when_model_server_is_offline(self):
        self.run_scenario(r'''
        saved.messages.push({role:'assistant',content:'saved answer'});
        window.pywebview.api.state = async () => ({ok:false,error:'Ollama offline'});
        await window.listeners.pywebviewready();
        assert.equal(elements.get('chat-title').textContent,'Chat A');
        assert.equal(elements.get('messages').children[0].children.at(-1).textContent,'saved answer');
        assert.equal(elements.get('send').disabled,true);
        assert.equal(elements.get('status').textContent,'Ollama offline');
        ''')

    def run_scenario(self, scenario):
        script = Path(__file__).parent / "webui" / "static" / "app.js"
        source = HARNESS.split("(async () => {", 1)[0] + "\n(async () => {\n" + scenario + r'''
        })().catch(error => {console.error(error); process.exitCode = 1;});
        '''
        result = subprocess.run([shutil.which("node"), "-e", source, str(script)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_inactive_chat_events_never_replace_selected_chat_or_draft(self):
        self.run_scenario(r'''
        const other = {id:'chat-b',title:'Chat B',model:'fake:7b',messages:[{role:'assistant',content:'B reply'}]};
        const records = new Map([['chat-a',saved],['chat-b',other]]);
        window.pywebview.api.list_chats = async () => ({ok:true,chats:[saved,other]});
        window.pywebview.api.open_chat = async id => ({ok:true,chat:structuredClone(records.get(id))});
        const settle = () => new Promise(resolve => setImmediate(resolve));
        const choose = title => elements.get('chats').children.find(item => item.children[0].textContent.startsWith(title)).children[0].listeners.click();
        const visible = () => elements.get('messages').children.map(node => node.textContent || node.children.map(child => child.textContent).join(' ')).join('\n');
        await window.listeners.pywebviewready();
        await choose('Chat B');
        elements.get('prompt').value = 'B draft';
        elements.get('status').textContent = 'B status';
        window.app.onEvent([{type:'token',chat_id:'chat-a',stream_id:'a1',text:'A partial'}]);
        assert.match(visible(), /B reply/);
        assert.doesNotMatch(visible(), /A partial/);
        saved.messages.push({role:'assistant',content:'A complete'});
        window.app.onEvent([{type:'done',chat_id:'chat-a',stream_id:'a1',text:'A complete'}]);
        await settle();
        assert.equal(elements.get('status').textContent,'B status');
        assert.equal(elements.get('prompt').value,'B draft');
        window.app.onEvent([{type:'token',chat_id:'chat-a',stream_id:'a1',text:'late token'}]);
        await choose('Chat A');
        assert.doesNotMatch(visible(), /late token/);
        window.app.onEvent([{type:'error',chat_id:'chat-a',stream_id:'a2',text:'recover me',saved:false,error:'disk full'}]);
        await settle();
        assert.match(visible(), /Not saved/);
        assert.match(visible(), /recover me/);
        await choose('Chat B');
        assert.equal(elements.get('prompt').value,'B draft');
        await choose('Chat A');
        assert.match(visible(), /recover me/);
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_reversed_chat_loads_and_old_same_chat_snapshots_are_ignored(self):
        self.run_scenario(r'''
        const other = {id:'chat-b',title:'Chat B',model:'other-model',messages:[]};
        const records = new Map([['chat-a',saved],['chat-b',other]]);
        const settle = () => new Promise(resolve => setImmediate(resolve));
        const choose = title => elements.get('chats').children.find(item => item.children[0].textContent.startsWith(title)).children[0].listeners.click();
        let hold = null, resolveOld;
        window.pywebview.api.list_chats = async () => ({ok:true,chats:[saved,other]});
        window.pywebview.api.open_chat = id => {
          const snapshot = {ok:true,chat:structuredClone(records.get(id))};
          if (id === hold) {hold=null; return new Promise(resolve => {resolveOld=()=>resolve(snapshot);});}
          return Promise.resolve(snapshot);
        };
        await window.listeners.pywebviewready();
        elements.get('prompt').value = 'A draft';
        hold = 'chat-b';
        const first = choose('Chat B');
        assert.equal(elements.get('send').disabled,true);
        assert.equal(elements.get('prompt').disabled,true);
        await choose('Chat A');
        resolveOld();
        await first;
        assert.equal(elements.get('chat-title').textContent,'Chat A');
        assert.equal(elements.get('prompt').value,'A draft');
        assert.equal(elements.get('model').value,'fake:7b');
        window.app.onEvent([{type:'token',stream_id:'a1',chat_id:'chat-a',text:'partial'}]);
        hold='chat-a';
        const second = choose('Chat A');
        saved.messages.push({role:'assistant',content:'newest reply'});
        window.app.onEvent([{type:'done',stream_id:'a1',chat_id:'chat-a',text:'newest reply'}]);
        await settle();
        resolveOld();
        await second;
        assert.equal(elements.get('messages').children.at(-1).children.at(-1).textContent,'newest reply');
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_completion_before_send_receipt_does_not_revive_generation(self):
        self.run_scenario(r'''
        await window.listeners.pywebviewready();
        elements.get('model').value='fake:7b';
        window.pywebview.api.send = async (...args) => {
          saved.messages.push({role:'user',content:args[1]}, {role:'assistant',content:'fast reply'});
          window.app.onEvent([{type:'token',chat_id:'chat-a',stream_id:'fast',text:'fast reply'},
                              {type:'done',chat_id:'chat-a',stream_id:'fast',text:'fast reply'}]);
          return {ok:true,stream_id:'fast'};
        };
        elements.get('prompt').value='Hi';
        await elements.get('composer').listeners.submit({preventDefault(){}});
        assert.equal(elements.get('status').textContent,'done');
        assert.equal(elements.get('stop').disabled,true);
        assert.equal(elements.get('send').disabled,false);
        assert.equal(elements.get('messages').children.length,2);
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_model_selection_streamed_text_and_stop(self):
        script = Path(__file__).parent / "webui" / "static" / "app.js"
        result = subprocess.run([shutil.which("node"), "-e", HARNESS, str(script)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
