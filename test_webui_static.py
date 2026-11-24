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
const timers = new Map();
let nextTimer = 0;
function setTimer(callback, delay) {
  const id = ++nextTimer;
  timers.set(id, {callback, delay});
  return id;
}
function runDraftTimers() {
  for (const [id, timer] of [...timers]) {
    if (timer.delay !== 700) continue;
    timers.delete(id);
    timer.callback();
  }
}
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
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document, window, Map, Error, String, Number, Boolean, Array,
  setTimeout: setTimer, clearTimeout: id => timers.delete(id),
});
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
    def test_models_finish_reenables_download_and_fast_done_stays_finished(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'models.js'),'utf8'),{document,window,Map,Set,Error,String,Number,Boolean,Array});
        window.pywebview.api.pull_model=async()=>({ok:true,pull_id:'normal'});
        document.getElementById('pull-other-name').value='normal';
        elements.get('pull-other').listeners.submit({preventDefault(){}});
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(elements.get('pull-other-button').disabled,true);
        window.app.onEvent([{type:'pull_done',pull_id:'normal',model:'normal'}]);
        assert.equal(elements.get('pull-other-button').disabled,false);
        window.pywebview.api.pull_model=async()=>{window.app.onEvent([{type:'pull_done',pull_id:'fast',model:'fast'}]);return {ok:true,pull_id:'fast'};};
        elements.get('pull-other-name').value='fast';
        elements.get('pull-other').listeners.submit({preventDefault(){}});
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(elements.get('pull-other-button').disabled,false);
        assert.equal(elements.get('pull-other-progress').children.length,0);
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_the_cube_switches_between_classic_and_cockpit_and_saves_the_choice(self):
        self.run_scenario(r'''
        const attributes={};
        const rootElement={setAttribute(name,value){attributes[name]=value;},getAttribute(name){return attributes[name];}};
        const saved=[];
        window.pywebview.api.web_preferences=async()=>({ok:true,settings:{look:'cockpit'}});
        window.pywebview.api.set_web_preferences=async value=>{saved.push(value);return {ok:true};};
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'cockpit.js'),'utf8'),
          {document:{...document,documentElement:rootElement,getElementById:document.getElementById.bind(document)},window,String,Error});
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(attributes['data-look'],'cockpit');  // the saved look is applied on start
        assert.equal(elements.get('setting-look').value,'cockpit');
        await elements.get('look').listeners.click();
        assert.equal(attributes['data-look'],'classic');
        assert.equal(elements.get('look').attributes['aria-pressed'],'false');
        assert.equal(JSON.stringify(saved.at(-1)),'{"look":"classic"}');
        elements.get('setting-look').value='cockpit';
        await elements.get('setting-look').listeners.change();
        assert.equal(attributes['data-look'],'cockpit');
        assert.equal(JSON.stringify(saved.at(-1)),'{"look":"cockpit"}');
        window.lookui.setLook('neon');
        assert.equal(attributes['data-look'],'classic');  // unknown looks fall back to classic
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_models_shared_by_a_paired_computer_appear_in_their_own_group_and_send_there(self):
        self.run_scenario(r'''
        window.pywebview.api.shared_ai_models=async()=>({ok:true,computers:[
          {id:'pc1',backend:'peer:pc1',address:'192.168.1.20',state:'ready',models:['qwen3:32b']},
          {id:'pc2',backend:'peer:pc2',address:'192.168.1.30',state:'offline',models:[]}]});
        await window.listeners.pywebviewready();
        for (let i=0;i<5;i++) await new Promise(resolve=>setImmediate(resolve));
        const groups=elements.get('model').children;
        const shared=groups.filter(group=>group.sharedComputer);
        assert.equal(shared.length,1);
        assert.equal(shared[0].label,'Paired computer 192.168.1.20');
        assert.equal(shared[0].children[0].value,JSON.stringify(['peer:pc1','qwen3:32b']));
        elements.get('model').value=JSON.stringify(['peer:pc1','qwen3:32b']);
        elements.get('prompt').value='Hi';
        await elements.get('composer').listeners.submit({preventDefault(){}});
        assert.deepEqual(calls.at(-1).slice(1),['Hi','qwen3:32b',null,'peer:pc1']);
        assert.equal(elements.get('footnote').textContent,'Runs on 192.168.1.20, over your home network. Answers can be wrong.');
        elements.get('model').value='fake:7b';
        await elements.get('model').listeners.change();
        assert.equal(elements.get('footnote').textContent,'Everything runs on this computer. Answers can be wrong.');
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_reading_sharing_settings_never_enables_the_listener(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'sharing.js'),'utf8'),{document,window,Map,Set,Error,String,Number,Boolean,Array,Date,Object});
        window.pywebview.api.sharing_state=async()=>({ok:true,enabled:false,host:null,peers:{}});
        window.pywebview.api.set_sharing=async (...args)=>{calls.push(['sharing',...args]);return {ok:true,job_id:'job'};};
        await window.sharingui.openSettings();
        assert(!calls.some(item=>item[0]==='sharing'));
        assert.equal(elements.get('sharing-enabled').checked,false);
        elements.get('sharing-usage').checked=false;
        elements.get('sharing-enabled').checked=true;document.getElementById('sharing-address').value='192.168.1.10';
        await elements.get('sharing-enabled').listeners.change();
        assert.deepEqual(calls.at(-1),['sharing',true,'192.168.1.10',false,false]);
        // Sharing AI is its own choice, and each paired device needs its own tick.
        window.pywebview.api.sharing_state=async()=>({ok:true,enabled:true,usage:false,ai:true,host:null,peers:{pc:{address:'192.168.1.20',port:1,name:'Studio-PC'}},clients:[{id:'abcdef0123456789',ai:false},{id:'fedcba9876543210',name:'Lucas-MacBook',ai:false}]});
        window.pywebview.api.allow_shared_ai=async (...args)=>{calls.push(['allow',...args]);return {ok:true,clients:[{id:'abcdef0123456789',ai:true}]};};
        await window.sharingui.openSettings();
        assert.equal(elements.get('sharing-ai').checked,true);
        assert.equal(elements.get('sharing-clients-box').hidden,false);
        assert.match(elements.get('sharing-clients').textContent,/Device abcdef01 may use/);
        assert.match(elements.get('sharing-clients').textContent,/Lucas-MacBook \(fedcba98\) may use/);
        assert.match(elements.get('sharing-peers').textContent,/^Studio-PC \(pc\) · 192.168.1.20:1/);
        const tick=elements.get('sharing-clients').children[0].children[0].children[0];
        tick.checked=true;
        await tick.listeners.change();
        assert.deepEqual(calls.at(-1),['allow','abcdef0123456789',true]);
        await elements.get('sharing-ai').listeners.change();
        assert.deepEqual(calls.at(-1),['sharing',true,'192.168.1.10',false,true]);
        let handled = false;
        elements.get('pair-address').listeners.paste({
          clipboardData:{getData:()=> ' 192.168.1.20:49152 '},
          preventDefault(){handled = true;}
        });
        assert(handled);
        assert.equal(elements.get('pair-address').value,'192.168.1.20');
        assert.equal(elements.get('pair-port').value,'49152');
        window.sharingui.onEvent({type:'job_done',operation:'sharing',result:{peer_id:'peer'}});
        assert.equal(elements.get('sharing-status').textContent,'Device paired securely. Open Compute to check live usage.');
        ''')

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
    def test_compute_window_reports_its_own_source_polls_and_toggles_fullscreen(self):
        self.run_scenario(r'''
        const dir=require('node:path').dirname(process.argv[1]);
        document.documentElement={dataset:{computeSource:'window'},setAttribute(){}};
        const timers=[];
        const context={document,window,Map,Set,Error,String,Number,Boolean,Array,Date,setInterval:(fn,ms)=>{timers.push([fn,ms]);return 1;}};
        vm.runInNewContext(fs.readFileSync(require('node:path').join(dir,'compute.js'),'utf8'),context);
        vm.runInNewContext(fs.readFileSync(require('node:path').join(dir,'security.js'),'utf8'),context);
        vm.runInNewContext(fs.readFileSync(require('node:path').join(dir,'sharing.js'),'utf8'),context);
        vm.runInNewContext(fs.readFileSync(require('node:path').join(dir,'compute-window.js'),'utf8'),context);
        const api=window.pywebview.api;
        api.set_compute_active=async (...args)=>{calls.push(['compute',...args]);return {ok:true,data:null};};
        api.compute_window_preferences=async()=>({ok:true,language:'en'});
        const data={snapshot:{cpu:{percent:40},ram:{},gpus:[]},values:{device:'Mac',system:'macOS',cpu:'M1 Max',cpu_usage:'40%',ram:'64 GB',gpus:[],loaded:['qwen3.5:9b'],installed:['qwen3.5:9b'],models_available:true},peers:[],error:''};
        api.compute_state=async()=>({ok:true,active:true,data});
        api.toggle_compute_fullscreen=async()=>{calls.push(['fullscreen']);return {ok:true,fullscreen:!calls.filter(c=>c[0]==='fullscreen').slice(1).length};};
        await window.computewindow.start();
        assert.deepEqual(calls.at(-1),['compute',true,'window']);
        assert.equal(timers.length,1);
        await timers[0][0]();
        assert.equal(elements.get('compute-cpu').textContent,'40%');
        assert.equal(elements.get('compute-cpu-meter').value,40);
        assert.equal(elements.get('cube-cpu').textContent,'40%');
        assert.equal(elements.get('cube-gpu').textContent,'–');
        assert.equal(elements.get('compute-gpu-sensors').hidden,true);
        data.snapshot.gpus=[{percent:8,temperature_c:31,power_w:19.12}];
        data.values.gpus=[{name:'NVIDIA GeForce RTX 4090',percent:'8.0%',memory:'17.2 GiB / 24.0 GiB'}];
        await window.computewindow.refresh();
        assert.equal(elements.get('compute-gpu-meter').value,8);
        assert.equal(elements.get('compute-gpu-sensors').hidden,false);
        assert.equal(elements.get('compute-gpu-sensors').textContent,'31 °C · 19.1 W');
        assert.equal(elements.get('cube-gpu-detail').textContent,'31 °C');
        assert.equal(elements.get('cube-model').textContent,'qwen3.5:9b');
        api.sharing_state=async()=>{calls.push(['sharing_state']);return {ok:true,enabled:false};};
        api.pair_device=async (...args)=>{calls.push(['pair',...args]);return {ok:true,job_id:'pair'};};
        api.respond_internet=async (...args)=>{calls.push(['respond',...args]);return {ok:true};};
        const pairing=elements.get('net-pairing');
        pairing.open=true;
        pairing.listeners.toggle();
        assert.deepEqual(calls.at(-1),['sharing_state']);
        for (const [id,value] of [['address','192.168.1.20'],['port','49152'],['code','0123456789abcdef'],['pin','012345']]) {
          document.getElementById('pair-'+id).value=value;
        }
        elements.get('pair-form').listeners.submit({preventDefault(){}});
        assert.deepEqual(calls.at(-1),['pair','192.168.1.20',49152,'0123456789abcdef','012345','compute']);
        window.app.onEvent([{type:'internet_approval',approval_id:'pair-approval',stage:1,remaining_seconds:3,request:{target:'192.168.1.20'}}]);
        assert.equal(elements.get('internet-dialog').open,true);
        await elements.get('internet-allow').listeners.click();
        assert.deepEqual(calls.at(-1),['respond','pair-approval','continue']);
        window.app.onEvent([{type:'internet_closed',approval_id:'pair-approval'},{type:'job_done',operation:'sharing',result:{peer_id:'peer'}}]);
        assert.equal(elements.get('internet-dialog').open,false);
        assert.equal(elements.get('sharing-status').textContent,'Device paired securely. Look for Live under Paired computers.');
        await elements.get('compute-fullscreen').listeners.click();
        assert.equal(elements.get('compute-fullscreen').textContent,'Exit full screen');
        await window.computewindow.toggleFullscreen();
        assert.equal(elements.get('compute-fullscreen').textContent,'Full screen');
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
        ids.clear()
        Page().feed((root / "compute-window.html").read_text(encoding="utf-8"))
        self.assertEqual(missing, [])
        self.assertEqual(nested, [])

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_update_controls_follow_native_job_results(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'parity.js'),'utf8'), {document,window,Map,Set,Error,String,Number,Boolean,Array,URL});
        const el = id => document.getElementById(id);
        const api = window.pywebview.api;
        const click = id => el(id).listeners.click();
        const done = (operation, id, result) => window.parityui.onEvent({type:'job_done',job_id:id,operation,result});
        api.check_updates = async () => ({ok:true,job_id:'check'});
        api.install_update = async () => ({ok:true,job_id:'install'});
        api.rollback_update = async () => ({ok:true,job_id:'restore'});
        api.restart_app = async () => { calls.push('restart'); return {ok:true}; };
        api.create_shortcut = async () => ({ok:true});
        await click('check-updates');
        done('updates','check',{differences:['AIassist.py'],can_install:false,reason:'use git pull'});
        assert(el('install-update').hidden);
        assert.match(el('update-result').textContent,/git pull/);
        await click('check-updates');
        done('updates','check',{differences:['AIassist.py'],can_install:true,reason:''});
        assert.equal(el('hourly-install').hidden,false);
        assert.match(el('update-result').textContent,/1/);
        await click('install-update');
        assert(el('check-updates').disabled);
        window.parityui.onEvent({type:'job_progress',job_id:'install',operation:'install_update',completed:5,total:10});
        assert.equal(el('update-progress').value,5);
        window.parityui.onEvent({type:'job_progress',job_id:'install',operation:'install_update',cancellable:false});
        assert(el('cancel-update').hidden);
        done('install_update','install',{can_rollback:true,command:'python -m pip install -r requirements.txt'});
        assert.match(el('update-result').textContent,/pip install/);
        assert.equal(el('rollback-update').hidden,false);
        await click('rollback-update'); done('rollback_update','restore',{can_rollback:false});
        assert(el('rollback-update').hidden);
        assert.equal(el('restart-update').hidden,false);
        await click('restart-update'); assert(calls.includes('restart'));
        await click('create-shortcut'); assert.match(el('shortcut-result').textContent,/shortcut created/);
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_reviewer_model_offers_served_colibri_models_and_explains_when_there_are_none(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'parity.js'),'utf8'), {document,window,Map,Set,Error,String,Number,Boolean,Array,URL});
        const el = id => document.getElementById(id);
        window.pywebview.api.update_status = async () => ({ok:true, can_rollback:false, restart_ready:false});
        await window.parityui.openSettings();
        assert.match(el('hybrid-model-hint').textContent, /Optional/);
        const hybrid = {enabled:false, review_model:'', manage_memory:true};
        window.parityui.onState({hybrid, colibri:{installed:false, models:[]}});
        assert.equal(el('hybrid-model-options').children.length, 0);
        assert.match(el('hybrid-model-hint').textContent, /Optional/);
        assert.match(el('hybrid-model').placeholder, /No Colibri model/);
        window.parityui.onState({hybrid, colibri:{installed:true, folder:'C:/colibri', state:'offline', models:[]}});
        assert.match(el('hybrid-model-hint').textContent, /Start Colibri in Models/);
        window.parityui.onState({hybrid, colibri:{installed:true, folder:'C:/colibri', state:'ready', models:['qwen-reviewer', '<b>odd</b>']}});
        assert.deepEqual(el('hybrid-model-options').children.map(option => option.value), ['qwen-reviewer', '<b>odd</b>']);
        assert.equal(el('hybrid-model-options').children[1].children.length, 0);
        assert.match(el('hybrid-model-hint').textContent, /serving now/);
        assert.match(el('hybrid-model').placeholder, /Choose a Colibri model/);
        ''')

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
        assert.equal(window.i18n.gate("Read Ollama's online model list"),"Leggere l'elenco online dei modelli Ollama");
        assert.equal(window.i18n.gate('Download qwen3:8b through Ollama'),'Scaricare qwen3:8b tramite Ollama');
        assert.equal(window.i18n.gate('Internet access was not approved, so it was blocked (Internet connection: ollama.com). Settings > Connection log lists every request.'),
          "L'accesso a internet non è stato approvato ed è stato bloccato (Connessione a internet: ollama.com). Impostazioni > Registro connessioni elenca ogni richiesta.");
        assert.equal(window.i18n.gate('ollama.com'),'ollama.com');
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
        api.recommend_models=async()=>({ok:true,hardware:{cpu:'CPU',ram_total_bytes:32*1024**3,gpus:[]},recommendations:[{name:'recommended',fit:'fits GPU',size_bytes:5.2e9}]});
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
        assert.match(elements.get('welcome-reason').textContent,/5\.2 GB download/);
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
    def test_welcome_stays_closed_when_a_paired_computer_shares_its_models(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'welcome.js'),'utf8'), {document,window,Map,Set,Error,String,Number,Boolean,Array});
        const api=window.pywebview.api;
        api.set_web_preferences=async()=>({ok:true});
        api.shared_ai_models=async()=>({ok:true,computers:[{id:'pc',state:'ready',models:['qwen3:32b']}]});
        // Without the app's own start-up, so only this call can open the welcome page.
        await window.welcomeui.onReady({engine:'Ollama',models:[],settings:{first_run_complete:true}});
        assert.notEqual(document.getElementById('welcome-view').hidden,false);
        api.shared_ai_models=async()=>({ok:true,computers:[{id:'pc',state:'offline',models:[]}]});
        await window.welcomeui.onReady({engine:'Ollama',models:[],settings:{first_run_complete:true}});
        assert.equal(elements.get('welcome-view').hidden,false);  // nothing to chat with: the welcome page opens
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_welcome_reads_saved_preferences_when_this_computer_has_no_engine(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'welcome.js'),'utf8'), {document,window,Map,Set,Error,String,Number,Boolean,Array});
        const api=window.pywebview.api;
        api.set_web_preferences=async()=>({ok:true});
        api.web_preferences=async()=>({ok:true,settings:{first_run_complete:true}});
        api.shared_ai_models=async()=>({ok:true,computers:[{id:'pc',state:'ready',models:['qwen3:32b']}]});
        await window.welcomeui.onReady(null);  // the state call failed: no Ollama here
        assert.notEqual(document.getElementById('welcome-view').hidden,false);
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_welcome_character_builds_with_blender_and_explains_when_missing(self):
        self.run_scenario(r'''
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'welcome.js'),'utf8'), {document,window,Map,Set,Error,String,Number,Boolean,Array});
        const api=window.pywebview.api;
        const options={height:['short','medium','tall'],build:['slim','average','broad'],hair_style:['none','short','bun'],accessory:['none','headphones'],skin:['light','medium']};
        api.set_web_preferences=async()=>({ok:true});
        api.recommend_models=async()=>({ok:true,hardware:{cpu:'CPU',gpus:[]},recommendations:[]});
        api.character_state=async()=>({ok:true,blender:false,choices:null,portrait:null,building:false,options});
        await window.listeners.pywebviewready();
        await window.welcomeui.onReady({engine:'Ollama',models:[],settings:{first_run_complete:false}});
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(elements.get('welcome-character').hidden,false);
        assert.equal(elements.get('character-create').hidden,true);
        assert.match(elements.get('character-status').textContent,/blender\.org/);
        api.character_state=async()=>({ok:true,blender:true,choices:null,portrait:null,building:false,options});
        api.build_character=async choices=>{calls.push(['character',choices]);return {ok:true,job_id:'c1'};};
        await window.welcomeui.loadCharacter();
        assert.equal(elements.get('character-create').hidden,false);
        assert.equal(elements.get('character-form').hidden,false);
        assert.equal(elements.get('character-skin').value,'light');
        assert.equal(elements.get('character-hair_style').children.at(-1).textContent,'Bun');
        elements.get('character-name').value='Luca';
        elements.get('character-accessory').value='headphones';
        await elements.get('character-create').listeners.click();
        const [, choices]=calls.find(item=>item[0]==='character');
        assert.equal(choices.name,'Luca');
        assert.equal(choices.accessory,'headphones');
        assert.equal(elements.get('character-create').disabled,true);
        window.app.onEvent([{type:'job_done',operation:'character',result:{portrait:'data:image/png;base64,AAAA'}}]);
        assert.equal(elements.get('character-portrait').hidden,false);
        assert.equal(elements.get('character-portrait').src,'data:image/png;base64,AAAA');
        assert.equal(elements.get('character-status').textContent,'Your character is ready.');
        assert.equal(elements.get('character-create').disabled,false);
        window.app.onEvent([{type:'job_done',operation:'character',result:{portrait:'data:image/png;base64,AAAA',files:{avatar:'/x/character.vrm'},folder:'/data/character'}}]);
        assert.match(elements.get('character-status').textContent,/Unreal Engine.*\/data\/character/);
        window.app.onEvent([{type:'job_error',operation:'character',error:'Blender could not finish the character.'}]);
        assert.equal(elements.get('character-status').textContent,'Blender could not finish the character.');
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

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_generate_view_preserves_render_when_switching_sections(self):
        self.run_scenario(r'''
        await window.listeners.pywebviewready();
        const el = id => document.getElementById(id);
        const api = window.pywebview.api;
        const root = require('node:path').dirname(process.argv[1]);
        class PhotoReader {
          readAsDataURL() {
            this.result = 'data:image/png;base64,cGhvdG8=';
            this.onload();
          }
        }
        window.i18n = {t: message => message};
        vm.runInNewContext(fs.readFileSync(require('node:path').join(root, 'photo-video.js'), 'utf8'), {
          document, window, FileReader: PhotoReader, Blob, URL, atob,
          setTimeout: callback => callback(),
        });
        api.start_photo_video = async () => ({ok: true, job: 'render'});
        let finish;
        api.photo_video_status = async () => new Promise(resolve => { finish = resolve; });

        el('nav-generate').listeners.click();
        assert.equal(el('generate-view').hidden, false);
        assert.equal(el('chat-view').hidden, true);
        assert.equal(el('nav-generate').attributes['aria-current'], 'page');
        el('photo-video-image').files = [{size: 20}];
        el('photo-video-prompt').value = 'The car moves.';
        const render = el('photo-video-form').listeners.submit({preventDefault() {}});
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(el('photo-video-progress').hidden, false);
        assert.equal(el('photo-video-generate').disabled, true);

        el('nav-chats').listeners.click();
        assert.equal(el('generate-view').hidden, true);
        assert.equal(el('chat-view').hidden, false);
        assert.equal(el('nav-generate').attributes['aria-current'], 'false');
        finish({ok: true, ready: true, video: 'Y2xpcA==', name: 'test.mp4'});
        await render;

        el('nav-generate').listeners.click();
        assert.equal(el('photo-video-empty').hidden, true);
        assert.equal(el('photo-video-preview').hidden, false);
        assert.equal(el('photo-video-progress').hidden, true);
        assert.equal(el('photo-video-generate').disabled, false);
        assert.equal(el('photo-video-prompt').value, 'The car moves.');

        let paused = false;
        el('photo-video-preview').pause = () => { paused = true; };
        el('nav-chats').listeners.click();
        assert.equal(paused, true);
        URL.revokeObjectURL(el('photo-video-preview').src);
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_quick_animation_loads_previews_and_saves_without_comfyui(self):
        self.run_scenario(r'''
        await window.listeners.pywebviewready();
        const el = id => document.getElementById(id);
        const canvas = el('quick-animation-canvas');
        const draws = [];
        let tracksStopped = false;
        canvas.captureStream = fps => {
          assert.equal(fps, 30);
          return {getTracks: () => [{stop() { tracksStopped = true; }}]};
        };
        const makeCanvas = document.createElement;
        document.createElement = tag => {
          const node = makeCanvas(tag);
          if (tag === 'canvas') node.getContext = () => ({drawImage() {}});
          return node;
        };
        class Reader {
          readAsDataURL(file) {
            this.result = `data:${file.type};base64,Y2xpcA==`;
            this.onload();
          }
        }
        class Picture {
          naturalWidth = 1200;
          naturalHeight = 600;
          async decode() {}
        }
        class Recorder {
          static isTypeSupported(type) { return type === 'video/mp4'; }
          constructor(stream, {mimeType}) { this.mimeType = mimeType; this.state = 'inactive'; }
          start() { this.state = 'recording'; }
          stop() {
            this.state = 'inactive';
            this.ondataavailable({data: new Blob(['clip'], {type: this.mimeType})});
            this.onstop();
          }
        }
        window.i18n = {t: message => message};
        window.MediaRecorder = Recorder;
        window.photoShader = {duration: 3, create: () => ({
          setImage(image) { assert.deepEqual([image.width, image.height], [720, 360]); },
          draw(effect, seconds) { draws.push([effect, seconds]); },
        })};
        const frames = new Map();
        let nextFrame = 0;
        const root = require('node:path').dirname(process.argv[1]);
        vm.runInNewContext(fs.readFileSync(require('node:path').join(root, 'quick-animation.js'), 'utf8'), {
          document, window, FileReader: Reader, Image: Picture, MediaRecorder: Recorder, Blob,
          performance: {now: () => 0},
          requestAnimationFrame: callback => { frames.set(++nextFrame, callback); return nextFrame; },
          cancelAnimationFrame: id => frames.delete(id),
        });
        el('photo-video-preview').pause = () => {};
        el('nav-generate').listeners.click();
        el('generate-wan-mode').listeners.click();
        assert.equal(el('generate-quick-panel').hidden, true);
        assert.equal(el('generate-wan-panel').hidden, false);
        el('generate-quick-mode').listeners.click();
        const input = el('quick-animation-image');
        const save = el('quick-animation-save');
        const play = el('quick-animation-play');
        el('quick-animation-effect').value = 'ripple';
        input.files = [{size: 20, type: 'image/png'}];
        await input.listeners.change();
        assert.equal(canvas.hidden, false);
        assert.deepEqual(draws.at(-1), ['ripple', 0]);

        play.listeners.click();
        assert.equal(frames.size, 1);
        el('nav-chats').listeners.click();
        assert.equal(frames.size, 0);
        el('nav-generate').listeners.click();
        window.pywebview.api.save_quick_animation = async (...args) => {
          calls.push(args);
          return {ok: true, path: '/video.mp4'};
        };
        const recording = save.listeners.click();
        assert.equal(save.disabled, true);
        const [id, tick] = frames.entries().next().value;
        frames.delete(id);
        tick(3000);
        await recording;
        assert.equal(tracksStopped, true);
        assert.equal(save.disabled, false);
        assert.deepEqual(calls, [['Y2xpcA==', 'video/mp4']]);
        assert.equal(el('quick-animation-status').textContent, 'Clip saved.');
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
    def test_agent_progress_does_not_replace_the_engine_status(self):
        self.run_scenario(r'''
        await window.listeners.pywebviewready();
        elements.get('status').textContent = 'ollama ready';
        const seen = [];
        window.devui = {onEvent: event => seen.push(event)};
        window.app.onEvent([{type:'status',task_id:'t1',text:'Developer agent is inspecting the project.'}]);
        assert.equal(elements.get('status').textContent,'ollama ready');
        assert.equal(seen.length, 1);
        ''')

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
    def test_status_updates_and_tokens_keep_existing_message_nodes(self):
        self.run_scenario(r'''
        saved.messages = [{role:'assistant',content:'Previous reply'}];
        window.pywebview.api.list_chats = async () => ({ok:true,chats:[{id:saved.id,title:saved.title}]});
        await window.listeners.pywebviewready();
        const transcript = elements.get('messages');
        const savedNode = transcript.children[0];
        const sidebar = elements.get('chats');
        const initialRow = sidebar.children[0];
        window.app.onEvent([{type:'status',engine:'Ollama',chat_id:'chat-a'}]);
        assert.equal(transcript.children[0], savedNode);
        assert.equal(sidebar.children[0], initialRow);
        window.app.onEvent([{type:'token',chat_id:'chat-a',stream_id:'s',text:'first'}]);
        const live = transcript.children[1];
        const activeRow = sidebar.children[0];
        window.app.onEvent([{type:'token',chat_id:'chat-a',stream_id:'s',text:' second'}]);
        assert.equal(transcript.children[0], savedNode);
        assert.equal(transcript.children[1], live);
        assert.match(live.textContent, /first second/);
        assert.equal(sidebar.children[0], activeRow);
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_sending_waits_for_older_draft_saves_and_keeps_new_input(self):
        self.run_scenario(r'''
        const api = window.pywebview.api;
        const settle = () => new Promise(resolve => setImmediate(resolve));
        let releaseSave;
        const blockedSave = new Promise(resolve => {releaseSave = resolve;});
        let sent = false;
        api.save_draft = async (id, text) => {
          if (text === 'First') await blockedSave;
          saved.draft = text;
          return {ok:true};
        };
        api.send = async (id, text) => {
          sent = true;
          saved.draft = '';
          saved.messages.push({role:'user',content:text});
          return {ok:true,stream_id:'s'};
        };
        await window.listeners.pywebviewready();
        window.app.setPrompt('First');
        runDraftTimers();
        await settle();
        const sending = elements.get('composer').listeners.submit({preventDefault(){}});
        await settle();
        assert.equal(sent, false);
        window.app.setPrompt('Next draft');
        releaseSave();
        await sending;
        assert.equal(saved.draft, '');
        assert.equal(elements.get('prompt').value, 'Next draft');
        runDraftTimers();
        await settle();
        assert.equal(saved.draft, 'Next draft');
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_draft_timers_are_per_chat_and_send_clears_its_timer(self):
        self.run_scenario(r'''
        const other = {id:'chat-b',title:'Chat B',model:'fake:7b',messages:[]};
        const records = new Map([['chat-a',saved],['chat-b',other]]);
        const api = window.pywebview.api;
        api.list_chats = async () => ({ok:true,chats:[saved,other]});
        api.open_chat = async id => ({ok:true,chat:structuredClone(records.get(id))});
        const writes = [];
        api.save_draft = async (...args) => { writes.push(args); return {ok:true}; };
        api.send = async (id, text) => {
          records.get(id).messages.push({role:'user',content:text});
          return {ok:true,stream_id:'s'};
        };
        await window.listeners.pywebviewready();
        window.app.setPrompt('Keep A');
        await elements.get('chats').children[1].children[0].listeners.click();
        window.app.setPrompt('Send B');
        await elements.get('composer').listeners.submit({preventDefault(){}});
        runDraftTimers();
        await new Promise(resolve => setImmediate(resolve));
        assert.deepEqual(writes, [['chat-a', 'Keep A']]);
        assert.equal(elements.get('prompt').value, '');
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_developer_receives_early_results_and_recovers_missing_events(self):
        self.run_scenario(r'''
        document.querySelector = () => null;
        vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'developer.js'),'utf8'), {document,window});
        const api = window.pywebview.api;
        api.new_chat = async () => ({ok:true,chat:{id:'chat-a'}});
        api.set_chat_model = async () => ({ok:true});
        api.list_checkpoints = async () => ({ok:true,checkpoints:[]});
        api.list_project_files = async () => ({ok:true,files:[]});
        api.run_agent = async () => {
          window.app.onEvent([{type:'agent_done',task_id:'fast',chat_id:'chat-a',text:'Finished'}]);
          return {ok:true,task_id:'fast'};
        };
        await window.listeners.pywebviewready();
        elements.get('model').value = 'fake:7b';
        elements.get('dev-project').value = '/project';
        const submit = async () => {
          elements.get('dev-task').value = 'Review project';
          await elements.get('dev-form').listeners.submit({preventDefault(){}});
        };
        await submit();
        assert.equal(elements.get('dev-status').textContent, 'Task finished');
        assert.equal(elements.get('dev-run').disabled, false);
        const plan = {type:'plan_approval',task_id:'slow',chat_id:'chat-a',approval_id:'p',text:'Review files.'};
        api.run_agent = async () => ({ok:true,task_id:'slow'});
        api.developer_task_state = async () => ({ok:true,approvals:[plan],result:null});
        await submit();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(elements.get('dev-status').textContent, 'Waiting for your plan approval');
        window.app.onEvent([plan]);
        assert.equal(elements.get('dev-thread').children.filter(node => node.className === 'dev-card plan-card').length, 1);
        api.developer_task_state = async () => ({ok:true,approvals:[],result:{type:'agent_stopped',task_id:'slow',chat_id:'chat-a'}});
        window.app.onEvent([{type:'status',engine:'Ollama',chat_id:'chat-a'}]);
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(elements.get('dev-status').textContent, 'Task stopped');
        assert.equal(elements.get('dev-stop').disabled, true);
        ''')

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_model_selection_streamed_text_and_stop(self):
        script = Path(__file__).parent / "webui" / "static" / "app.js"
        result = subprocess.run([shutil.which("node"), "-e", HARNESS, str(script)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
