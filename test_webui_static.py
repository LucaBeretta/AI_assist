"""Exercise the bare frontend with a small DOM/API double, without a WebView."""

from pathlib import Path
from html.parser import HTMLParser
import json
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
    def test_hourly_preview_does_not_enable_audio_and_model_reflection_is_plain_text(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'hourly.js'),'utf8'),{document,window,Map,Set,Error,String,Number,Boolean,Array,Date});
        window.pywebview.api.preview_hourly_checkin=async()=>{calls.push(['preview']);return {ok:true,enabled:false,collecting:true};};
        window.pywebview.api.set_hourly_checkin=async value=>{calls.push(['enable',value]);return {ok:true,enabled:value};};
        await elements.get('hourly-preview').listeners.click();
        assert(!calls.some(item=>item[0]==='enable'));
        window.app.onEvent([{type:'hourly_report',report:{threads:1,topics:['Project'],system:'RAM',ollama:'ready',updates:'Not checked',reflection:'<script>bad</script>',reflected_by_model:true}}]);
        assert.equal(elements.get('hourly-reflection').textContent,'<script>bad</script>');
        assert.equal(elements.get('hourly-dialog').open,true);
        assert.equal(elements.get('hourly-enabled').checked,undefined);
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_compute_pauses_when_hidden_and_does_not_invent_missing_metrics(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'compute.js'),'utf8'),{document,window,Map,Set,Error,String,Number,Boolean,Array,Date});
        window.pywebview.api.set_compute_active=async active=>{calls.push(['compute',active]);return {ok:true,data:null};};
        await window.computeui.onVisible(true);
        window.app.onEvent([{type:'compute',data:{snapshot:{cpu:{percent:null},ram:{},gpus:[]},values:{device:'Local',system:'Windows',cpu:'CPU',cpu_usage:'unavailable',ram:'unavailable',gpus:[],loaded:[],installed:[],models_available:false},peers:[],error:''}}]);
        assert.equal(elements.get('compute-cpu').textContent,'unavailable');
        assert.equal(elements.get('compute-cpu-meter').hidden,true);
        await window.computeui.onVisible(false);
        assert.deepEqual(calls.at(-1),['compute',false]);
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_internet_dialog_blocks_on_enter_escape_and_close_and_escapes_details(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'security.js'),'utf8'),{document,window,Map,Set,Error,String,Number,Boolean,Array,Date});
        window.pywebview.api.respond_internet=async(id,action)=>{calls.push(['internet',id,action]);return {ok:true};};
        const event={type:'internet_approval',approval_id:'a',stage:1,remaining_seconds:3,request:{kind:'Download',target:'<script>bad</script>',purpose:'Get a model',sends:'Model name',source:'test',scope:'one task'}};
        window.app.onEvent([event]);
        assert.equal(elements.get('internet-target').textContent,'<script>bad</script>');
        assert.equal(elements.get('internet-allow').disabled,false);
        await elements.get('internet-allow').listeners.click();
        assert.deepEqual(calls.at(-1),['internet','a','continue']);
        window.app.onEvent([{...event,stage:2}]);
        assert.equal(elements.get('internet-allow').disabled,true);
        for(const key of ['Enter','Escape']) {
          await elements.get('internet-dialog').listeners.keydown({key,preventDefault(){}});
          assert.deepEqual(calls.at(-1),['internet','a','block']);
        }
        await elements.get('internet-dialog').listeners.close();
        assert.deepEqual(calls.at(-1),['internet','a','block']);
        window.app.onEvent([{type:'internet_closed',approval_id:'a',allowed:false}]);
        assert.equal(elements.get('internet-dialog').open,false);
        ''')

    def test_every_authored_translation_id_exists_and_forms_are_not_nested(self):
        root = Path(__file__).parent / "webui" / "static"
        source = (root / "strings.js").read_text(encoding="utf-8")
        def unique(pairs):
            self.assertEqual(len(pairs), len(dict(pairs)), "Translation IDs must be unique")
            return dict(pairs)
        messages = json.loads(source.split("const it = ", 1)[1].split(";\n  const en", 1)[0], object_pairs_hook=unique)
        ids, missing, nested = set(), [], []
        class Page(HTMLParser):
            form = False
            def handle_starttag(self, tag, attrs):
                for key, value in attrs:
                    if key == "id":
                        if value in ids:
                            nested.append("Duplicate ID: " + value)
                        ids.add(value)
                    if key.startswith("data-i18n") and value not in messages:
                        missing.append(value)
                if tag == "form":
                    if self.form:
                        nested.append("Nested form")
                    self.form = True
            def handle_endtag(self, tag):
                if tag == "form":
                    self.form = False
        Page().feed((root / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(missing, [])
        self.assertEqual(nested, [])

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_parity_feedback_capabilities_and_deploy_confirmation(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'parity.js'),'utf8'), {document,window,Map,Set,Error,String,Number,Boolean,Array,URL});
        const api=window.pywebview.api;
        saved.messages=[{role:'assistant',content:'Old reply'}];
        api.prepare_feedback=async(...args)=>{calls.push(['feedback',...args]);return {ok:true,text:'Prepared follow-up'};};
        api.model_capabilities=async()=>({ok:true,metadata:{capabilities:['tools'],context_length:32768}});
        api.check_updates=async()=>{
          window.app.onEvent([{type:'job_done',job_id:'u1',operation:'updates',result:{differences:[],download:'https://example.com/source.zip'}}]);
          return {ok:true,job_id:'u1'};
        };
        api.deploy_vercel=async(...args)=>{calls.push(['deploy',...args]);return {ok:true,job_id:'d1'};};
        api.cancel_job=async id=>{calls.push(['cancel',id]);return {ok:true};};
        await window.listeners.pywebviewready();
        window.parityui.openFeedback('chat-a',0); elements.get('feedback-text').value='Use exact numbers';
        await elements.get('feedback-form').listeners.submit({preventDefault(){}});
        assert.equal(elements.get('prompt').value,'Prepared follow-up');
        assert(!calls.some(item=>item[0]==='chat-a'));
        await window.parityui.capabilities();
        assert.match(elements.get('capabilities-result').textContent,/Reported/);
        await elements.get('check-updates').listeners.click();
        assert.match(elements.get('update-result').textContent,/matches upstream/);
        assert.equal(elements.get('check-updates').disabled,false);
        await elements.get('deploy-form').listeners.submit({preventDefault(){}});
        assert(!calls.some(item=>item[0]==='deploy'));
        elements.get('deploy-confirm').checked=true; document.getElementById('deploy-folder').value='C:/site'; document.getElementById('deploy-project').value='site';
        await elements.get('deploy-form').listeners.submit({preventDefault(){}});
        await new Promise(resolve=>setImmediate(resolve));
        assert(calls.some(item=>item[0]==='deploy'&&item[1]==='C:/site'));
        await elements.get('deploy-stop').listeners.click();
        assert.deepEqual(calls.at(-1),['cancel','d1']);
        window.app.onEvent([{type:'job_done',operation:'deploy',job_id:'d1',result:{url:'javascript:alert(1)'}}]);
        assert.equal(elements.get('deploy-result').children[0].tagName,'span');
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_locales_translate_controls_but_never_model_or_user_text(self):
        self.run_scenario(r'''
        const load=name=>vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),name),'utf8'),{document,window,Map,Set,Error,String,Number,Boolean,Array});
        window.navigator={language:'it-IT'};
        load('strings.js');
        assert.equal(window.i18n.language,'it');
        assert.equal(window.i18n.t('New chat'),'Nuova chat');
        assert.equal(window.i18n.t('Thought for {v0} s',{v0:3}),'Ha ragionato per 3 s');
        for(const [key,value] of Object.entries(window.i18n.messages.it)) {
          assert.deepEqual((key.match(/\{\w+\}/g)||[]).sort(),(value.match(/\{\w+\}/g)||[]).sort());
        }
        saved.messages=[{role:'assistant',content:'New chat\n<script>unsafe</script>'}];
        await window.listeners.pywebviewready();
        assert.match(elements.get('messages').textContent,/New chat/);
        assert.match(elements.get('messages').textContent,/<script>unsafe<\/script>/);
        assert.equal(elements.get('messages').children[0].children[1].children[0].textContent,'Copia');
        window.i18n.setLanguage('en'); assert.equal(window.i18n.t('New chat'),'New chat');
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_thinking_keyboard_groups_and_saved_zoom(self):
        self.run_scenario(r'''
        document.documentElement={style:{}};
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'premium.js'),'utf8'), {document,window,Map,Set,Error,String,Number,Boolean,Array,Date});
        const p=window.premiumui;
        assert.equal(p.splitThinking('<think>reason</think>Answer').answer,'Answer');
        assert.equal(p.splitThinking('<think>partial').pending,true);
        assert.equal(p.splitThinking('```html\n<think>literal</think>\n```').thinking,'');
        const rendered=p.renderReply('<think>reason</think>Answer',2.2);
        assert.equal(rendered.children[0].tagName,'details');
        assert.equal(rendered.children[0].children[0].textContent,'Thought for 2 s');
        const now=new Date('2026-11-19T12:00:00');
        assert.equal(p.dateGroup('2026-11-19T01:00:00',now),'Today');
        assert.equal(p.dateGroup('2026-11-18T12:00:00',now),'Yesterday');
        assert.equal(p.dateGroup('2026-11-15T12:00:00',now),'Last 7 days');
        assert.equal(p.dateGroup('2026-11-01T12:00:00',now),'Older');
        window.pywebview.api.set_web_preferences=async value=>{calls.push(['zoom',value.zoom]);return {ok:true};};
        p.onState({settings:{zoom:1.2}});
        p.key({ctrlKey:true,key:'+',preventDefault(){}});
        assert.equal(document.documentElement.style.zoom,'1.3');
        assert.deepEqual(calls.at(-1),['zoom',1.3]);
        p.key({ctrlKey:true,key:'0',preventDefault(){}});
        assert.equal(document.documentElement.style.zoom,'1');
        p.key({ctrlKey:true,key:'/',preventDefault(){}});
        assert.equal(elements.get('shortcuts-dialog').open,true);
        saved.messages=[{role:'user',content:'Edit this'},{role:'assistant',content:'answer'}];
        await window.listeners.pywebviewready();
        elements.get('prompt').value='';
        elements.get('prompt').listeners.keydown({key:'ArrowUp',preventDefault(){}});
        assert.equal(elements.get('prompt').value,'Edit this');
        assert.equal(elements.get('edit-hint').hidden,false);
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_welcome_handles_progress_fast_completion_open_and_skip(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'welcome.js'),'utf8'), {document,window,Map,Set,Error,String,Number,Boolean,Array});
        const api=window.pywebview.api;
        api.set_web_preferences=async value=>{calls.push(['preferences',value.first_run_complete]); return {ok:true};};
        api.recommend_models=async()=>({ok:true,hardware:{cpu:'CPU',ram_total_bytes:32*1024**3,gpus:[]},recommendations:[{name:'recommended',fit:'fits GPU'}]});
        api.pull_model=async name=>{
          window.app.onEvent([{type:'pull_progress',pull_id:'p1',model:name,completed:5,total:10,status:'downloading'},
                              {type:'pull_done',pull_id:'p1',model:name}]);
          return {ok:true,pull_id:'p1'};
        };
        api.set_chat_model=async(...args)=>{calls.push(['select',...args]);return {ok:true};};
        api.state=async()=>({ok:true,engine:'Ollama',host:'localhost',models:['fake:7b'],settings:{first_run_complete:true}});
        await window.listeners.pywebviewready();
        await window.welcomeui.onReady({engine:'Ollama',models:[],settings:{first_run_complete:false}});
        assert.equal(elements.get('welcome-view').hidden,false);
        assert.match(elements.get('welcome-hardware').textContent,/32.0 GB RAM/);
        await elements.get('welcome-start').listeners.click();
        assert.equal(elements.get('welcome-progress').value,5);
        assert.equal(elements.get('welcome-open').hidden,false);
        assert.equal(elements.get('welcome-stop').hidden,true);
        await elements.get('welcome-open').listeners.click();
        assert.equal(elements.get('chat-view').hidden,false);
        assert(calls.some(item=>item[0]==='select'&&item[2]==='recommended'));
        await elements.get('welcome-skip').listeners.click();
        assert(calls.some(item=>item[0]==='preferences'&&item[1]===true));
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_d5_memory_prompts_status_and_backend_selection(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'workspace.js'),'utf8'), {document,window,Map,Error,String,Number,Boolean,Array});
        const api=window.pywebview.api;
        saved.environment={instructions:'Be precise',memory_ids:[]};
        api.list_memories=async()=>({ok:true,enabled:true,memories:[{id:'m1',text:'Keep numbers exact'}]});
        api.set_memory_enabled=async value=>{calls.push(['enabled',value]);return {ok:true};};
        api.add_memory=async text=>{calls.push(['memory',text]);return {ok:true};};
        api.list_prompts=async()=>({ok:true,prompts:[{name:'review',text:'Review the current code'}]});
        api.set_chat_instructions=async(id,text)=>{calls.push(['instructions',id,text]);return {ok:true};};
        api.set_chat_memories=async(id,ids)=>{calls.push(['memories',id,ids]);return {ok:true};};
        api.state=async()=>({ok:true,engine:'Ollama',host:'localhost',models:['fake:7b'],colibri:{installed:true,running:true,models:['served']}});
        await window.listeners.pywebviewready();
        window.workspaceui.openSettings(); await new Promise(resolve=>setImmediate(resolve));
        elements.get('memory-enabled').checked=false;
        await elements.get('memory-enabled').listeners.change();
        assert.deepEqual(calls.at(-1),['enabled',false]);
        elements.get('memory-add').listeners.click(); elements.get('text-dialog-input').value='New note';
        await elements.get('text-dialog-form').listeners.submit({preventDefault(){}});
        assert.deepEqual(calls.at(-1),['memory','New note']);
        await window.workspaceui.openInstructions('chat-a'); elements.get('text-dialog-input').value='New rules';
        await elements.get('text-dialog-form').listeners.submit({preventDefault(){}});
        assert(calls.some(item=>item[0]==='instructions'&&item[2]==='New rules'));
        await window.workspaceui.openMemories('chat-a');
        await elements.get('chat-memory-form').listeners.submit({preventDefault(){}});
        assert.equal(calls.find(item=>item[0]==='memories')[2].length,0);
        elements.get('prompt').value='/rev'; elements.get('prompt').listeners.input();
        assert.equal(window.workspaceui.promptKey({key:'Enter',preventDefault(){}}),true);
        assert.equal(elements.get('prompt').value,'Review the current code');
        window.app.onEvent([{type:'status',chat_id:'chat-a',ok:true,engine:'ollama ready',model_name:'fake:7b',gpu_fraction:.67,cpu_fraction:.33,privacy_kind:'remote server',fraction:.8}]);
        assert.equal(elements.get('status-placement').textContent,'67% gpu · 33% cpu');
        assert.equal(elements.get('status-privacy').className,'privacy is-remote');
        assert.equal(elements.get('model').children[1].label,'Colibri');
        elements.get('model').value=JSON.stringify(['Colibri','served']);
        elements.get('prompt').value='Colibri request';
        await elements.get('composer').listeners.submit({preventDefault(){}});
        assert(calls.some(item=>item[2]==='served'&&item[4]==='Colibri'));
        window.pywebview.api.create_project=async (...args)=>{calls.push(['project',...args]);return {ok:true,project:{id:'p'}};};
        document.getElementById('project-name').value='Colibri project';
        document.getElementById('project-instructions').value='Keep the setup';
        document.getElementById('project-folder').value='';
        document.getElementById('project-use-env').checked=true;
        await elements.get('project-form').listeners.submit({preventDefault(){}});
        const environment=calls.find(item=>item[0]==='project').at(-1);
        assert.equal(environment.backend,'Colibri'); assert.equal(environment.model,'served');
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_developer_can_pin_before_any_task_and_code_can_be_saved(self):
        self.run_scenario(r'''
        Element.prototype.getAttribute=function(name){return this.attributes[name];};
        const api=window.pywebview.api;
        api.new_chat=async()=>({ok:true,chat:{id:'developer'}});
        api.set_chat_project=async(...args)=>{calls.push(['folder',...args]);return {ok:true};};
        api.list_project_files=async()=>({ok:true,files:[{path:'main.py',estimated_tokens:20}]});
        api.list_checkpoints=async()=>({ok:true,checkpoints:[]});
        api.pin_file=async(...args)=>{calls.push(['pin',...args]);return {ok:true,pinned_files:[{path:'main.py',estimated_tokens:20}]};};
        api.save_code=async(...args)=>{calls.push(['code',...args]);return {ok:true};};
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'developer.js'),'utf8'), {document,window,Map,Set,Error,String,Number,Boolean,Array});
        await window.devui.setProject('C:/project');
        assert.deepEqual(calls.at(-1),['folder','developer','C:/project']);
        const pin=elements.get('dev-files').children[0].children.at(-1);
        assert.equal(pin.disabled,false); await pin.listeners.click();
        assert.deepEqual(calls.at(-1),['pin','developer','main.py']);
        const code=new Element('pre'); code.textContent='print(1)'; code.firstChild={className:'lang-python'};
        const action=new Element('button'); action.setAttribute('data-action','save'); action.parentElement={nextElementSibling:code};
        elements.get('messages').listeners.click({target:{closest:()=>action}});
        await new Promise(resolve=>setImmediate(resolve));
        assert.deepEqual(calls.at(-1),['code','print(1)','code.py']);
        ''')

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
    def test_loading_feedback_is_visible_only_for_the_selected_chat(self):
        self.run_scenario(r'''
        await window.listeners.pywebviewready();
        window.app.onEvent([{type:'model_loading',chat_id:'chat-a',model:'cold',elapsed_seconds:12.4}]);
        assert.equal(elements.get('status').textContent,'Loading cold… 12 s');
        window.app.onEvent([{type:'model_loading',chat_id:'chat-b',model:'other',elapsed_seconds:20}]);
        assert.equal(elements.get('status').textContent,'Loading cold… 12 s');
        window.app.onEvent([{type:'model_loaded',chat_id:'chat-a',model:'cold',load_seconds:15.25}]);
        assert.match(elements.get('status').textContent, /cold ready in 15\./);
        ''')

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
