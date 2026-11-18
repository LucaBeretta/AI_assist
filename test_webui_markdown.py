"""Markdown tests against a DOM double that rejects innerHTML writes."""

from pathlib import Path
import shutil
import subprocess
import unittest

from test_webui_static import HARNESS


CHECKS = r'''
const window = {};
Object.defineProperty(Element.prototype,'innerHTML',{set(){throw new Error('innerHTML is forbidden');}});
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{document,window});
const render = text => window.renderMarkdown(text);
const walk = root => [root,...root.children.flatMap(walk)];
const tags = (root,name) => walk(root).filter(node => node.tagName===name);
const checkSafe = root => {
  for (const element of walk(root)) {
    assert.ok(!['script','img','iframe','object','svg','math','style'].includes(element.tagName));
    for (const key of Object.keys(element.attributes)) assert.ok(!key.toLowerCase().startsWith('on'));
  }
};
for (const text of ['<script>alert(1)</script>','<img src=x onerror=alert(1)>',
                    '<svg onload=alert(1)>','<iframe srcdoc="evil">']) {
  const fragment=render(text);
  assert.equal(fragment.nodeType,11);
  assert.equal(fragment.textContent,text);
  checkSafe(fragment);
}
for (const url of ['javascript:alert(1)','JaVaScRiPt:alert(1)','data:text/html,evil','file:///private',
                   '//example.test','javascript&#58;alert(1)','java\tscript:alert(1)']) {
  const input='[bad]('+url+')';
  const fragment=render(input);
  assert.equal(tags(fragment,'a').length,0,url);
  assert.equal(fragment.textContent,input);
  checkSafe(fragment);
}
for (const url of ['https://example.test/a_(b)','http://example.test','mailto:someone@example.test']) {
  const link=tags(render('[**link**]('+url+')'),'a')[0];
  assert.equal(link.attributes.href,url);
  assert.match(link.attributes.rel,/noopener/);
  assert.equal(link.attributes.target,'_blank');
}
const normal=render('# Heading\n\nA **bold** and *italic* and `inline` paragraph.\n\n> quoted\n\n---');
assert.equal(tags(normal,'h1')[0].textContent,'Heading');
assert.equal(tags(normal,'strong')[0].textContent,'bold');
assert.equal(tags(normal,'em')[0].textContent,'italic');
assert.equal(tags(normal,'code')[0].textContent,'inline');
assert.equal(tags(normal,'blockquote')[0].textContent,'quoted');
assert.equal(tags(normal,'hr').length,1);
const table=render('| A | B |\n| :--- | ---: |\n| **bold** | a\\|b |\n| `x|y` | z |');
assert.equal(tags(table,'table').length,1);
assert.equal(tags(table,'th').length,2);
assert.equal(tags(table,'td').length,4);
assert.equal(tags(table,'td')[1].textContent,'a|b');
assert.equal(tags(table,'code')[0].textContent,'x|y');
const nested=render('- parent\n  - child\n    1. numbered\n- sibling');
assert.equal(tags(nested,'ul').length,2);
assert.equal(tags(nested,'ol').length,1);
assert.equal(tags(nested,'li').length,4);
const html=render('```html\n<img src=x onerror=alert(1)>\n<script>alert(1)</script>\n```');
checkSafe(html);
assert.equal(tags(html,'code')[0].className,'lang-html');
assert.ok(tags(html,'code')[0].textContent.includes('<img src=x onerror=alert(1)>'));
assert.equal(tags(html,'button').length,3);
assert.equal(tags(html,'div').filter(node=>node.className==='code-actions').length,1);
const open=render('```js\nconst x = "<img onerror=evil>";');
checkSafe(open);
assert.equal(tags(open,'pre').length,1);
assert.equal(tags(open,'code')[0].textContent,'const x = "<img onerror=evil>";');
for (const [lang,code] of [['py','def f(): # note\n\treturn "x" + 42'],
                           ['js','const x = "y"; // note\nreturn 1;'],
                           ['ts','interface User { id: number; }'],['json','{"ok": true, "n": 2}'],
                           ['css','a { margin: 2px; /* note */ }'],['bash','if true; then echo "x"; fi # note'],
                           ['powershell','function Test { return "x" } # note']]) {
  const block=render('```'+lang+'\n'+code+'\n```');
  assert.equal(tags(block,'code')[0].textContent,code);
  assert.ok(tags(block,'span').length>0,lang);
  checkSafe(block);
}
assert.ok(tags(open,'span').some(node=>node.className==='tok-kw'));
assert.ok(tags(open,'span').some(node=>node.className==='tok-str'));
checkSafe(render('```html" onclick="evil\nraw\n```'));
checkSafe(render('```__proto__\nraw\n```'));
assert.doesNotThrow(()=>render('>'.repeat(80)+' deepest'));
assert.equal(render('prefix '+ '`'.repeat(10000)).textContent,'prefix '+ '`'.repeat(10000));
console.log('Markdown safety, structure, partial fences and highlighting passed');
'''


class MarkdownTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the DOM test")
    def test_markdown_features_and_xss_are_safe_dom_nodes(self):
        source = HARNESS.split("const chat =",1)[0] + CHECKS
        script = Path(__file__).parent / "webui" / "static" / "markdown.js"
        result = subprocess.run([shutil.which("node"),"-e",source,str(script)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__ == "__main__":
    unittest.main()
