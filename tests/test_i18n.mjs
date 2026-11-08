import assert from 'node:assert/strict';
import fs from 'node:fs';
import {zhCN} from '../dashboard/i18n-messages.js';
import {translate, getLanguage, setLanguage, setText, setAttributeText} from '../dashboard/i18n.js';
assert.equal(getLanguage(), 'zh-CN');
const examples = [
 ['Play', '播放'],
 ['Held fingertip acceleration · peak 43.8 m/s² · RMS 2.4 m/s². Measured from this take; includes no wrist rotation or airborne motion.', '按住时指尖加速度 · 峰值 43.8 m/s² · 均方根 2.4 m/s²。测量自此回放，不含转腕和腾空移动。'],
 ['Video ready · 640 × 480 · up to 30 fps with game audio.', '视频已就绪 · 640 × 480 · 最高 30 fps，含游戏音频。'],
 ['270 charts · grouped by song and difficulty.', '共 270 张谱面 · 按歌曲和难度分组。'],
 ['56.55–75.00 s · 18.45 s selected', '56.55–75.00 s · 已选 18.45 秒'],
 ['15 hits · 0 misses · score 1,234 · 3 planned arc reacquisitions', '命中 15 · 漏键 0 · 分数 1,234 · 规划续接蛇 3 次'],
 ['Searching touchpoint plans · 14.5% · 30 / 200 items', '正在搜索触点方案 · 14.5% · 30 / 200 个目标'],
 ['Recording preview · 3.2 / 10.0 s', '正在录制预览 · 3.2 / 10.0 s'],
 ['Live preview could not load: Tablet dimensions do not match the contact plan.', '无法加载实时预览：平板尺寸与触点规划不一致。'],
 ['76 / 76 planned arc catches confirmed by native replay. Also caught 2 additional arc ticks.', '原生回放已确认 76 / 76 个规划中的蛇计分点，另接住了 2 个计分点。'],
 ['Welcome, Queen of Fiction.', 'Welcome, Queen of Fiction.'],
 ['红莲 / Arghena Future 11', '红莲 / Arghena Future 11'],
 ['<script>alert(1)</script>', '<script>alert(1)</script>'],
];
for (const [english,chinese] of examples) {
 assert.equal(translate(english), chinese, english);
 assert.equal(translate(english,'en'), english);
}
// Every formatted translation retains the same data fields, with no invented values.
for (const [source,target] of Object.entries(zhCN)) {
 const slots = text => [...text.matchAll(/\{\w+\}/g)].map(m=>m[0]).sort();
 assert.deepEqual(slots(target), slots(source), source);
}
// Full catalog coverage for original HTML text and accessible names.
const html=fs.readFileSync(new URL('../dashboard/index.html',import.meta.url),'utf8');
const stripped=html.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/g,'');
const exempt = /^(?:简体中文|English|\d[:\d. /]*|[°▷])$/;
for(const value of [...stripped.matchAll(/>([^<>]+)</g)].map(m=>m[1].trim()).filter(Boolean)) {
 assert(Object.hasOwn(zhCN,value)||exempt.test(value), 'Missing HTML translation: '+value);
}
for(const m of stripped.matchAll(/(?:aria-label|title|placeholder)="([^"]+)"/g)) assert(Object.hasOwn(zhCN,m[1]),m[1]);
// Display bindings never touch form values; Chinese <-> English uses the source,
// and later updates keep following the chosen locale without reloading a take.
const element={textContent:'',isConnected:true,value:'2.5',setAttribute(name,value){this[name]=value;}};
setText(element,'Play'); setAttributeText(element,'aria-label','Play preview');
assert.equal(element.textContent,'播放');assert.equal(element['aria-label'],'播放预览');
setLanguage('en');assert.equal(element.textContent,'Play');
assert.equal(element['aria-label'],'Play preview');
assert.equal(element.value,'2.5');
setLanguage('zh-CN');assert.equal(element.textContent,'播放');
assert.equal(element['aria-label'],'播放预览');
console.log(`i18n: ${Object.keys(zhCN).length} messages, templates, HTML/ARIA coverage and source-preserving bindings passed.`);
