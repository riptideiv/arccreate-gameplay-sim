import { zhCN } from './i18n-messages.js';

const STORAGE_KEY = 'rhythm-workbench.language';
let language = 'zh-CN';
try { if ((typeof window === 'undefined' ? null : window.localStorage)?.getItem(STORAGE_KEY) === 'en') language = 'en'; } catch { /* Private browsing can block storage. */ }
const templates = Object.entries(zhCN).filter(([key]) => key.includes('{')).map(([key, value]) => {
  const names = [];
  const parts = key.split(/(\{\w+\})/g).map(part => {
    if (/^\{\w+\}$/.test(part)) {
      const name = part.slice(1,-1); names.push(name);
      // Numeric slots cannot swallow other chunks in a composed status line.
      return ['error','features','title'].includes(name) ? '(.+?)'
        : name === 'format' ? '(MP4|WEBM)' : name === 'dimensions' ? '(\\d+ × \\d+)'
        : '([\\d.,:+-]+)';
    }
    return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  });
  return { regex: new RegExp('^' + parts.join('') + '$'), names, value,
    specificity: key.replace(/\{\w+\}/g, '').length };
}).sort((a,b) => b.specificity - a.specificity);

// No HTML substitution: chart metadata, numbers and errors remain ordinary text.
export function translate(source, locale = language) {
  const text = String(source ?? '');
  if (locale === 'en') return text;
  if (Object.hasOwn(zhCN, text)) return zhCN[text];
  // Composed status messages use a shared separator. Preserve original numbers/titles.
  // Try whole-message templates first (e.g. export dimensions and range selection).
  for (const entry of templates) {
    const match = text.match(entry.regex);
    if (!match) continue;
    const captures = Object.fromEntries(entry.names.map((name, i) => [name, match[i+1]]));
    return entry.value.replace(/\{(\w+)\}/g, (_,name) =>
      name === 'error' ? translate(captures[name], locale) : name === 'features' ? captures[name].split(', ').map(part => translate(part, locale)).join('、') : captures[name]);
  }
  if (text.includes(' · ')) return text.split(' · ').map(part => translate(part, locale)).join(' · ');
  return text;
}

const textBindings = new Map();
const attributeBindings = new Map();
const staticNodes = new Map();
let initialized = false;
export const getLanguage = () => language;
export function setText(element, source) {
  const original = String(source ?? '');
  textBindings.set(element, original);
  const translated = translate(original);
  if (element.textContent !== translated) element.textContent = translated;
}
export function setAttributeText(element, attribute, source) {
  if (!attributeBindings.has(element)) attributeBindings.set(element, new Map());
  attributeBindings.get(element).set(attribute, String(source ?? ''));
  element.setAttribute(attribute, translate(source));
}
function renderBindings() {
  for (const [node, original] of staticNodes) {
    if (!node.isConnected) { staticNodes.delete(node); continue; }
    const [, leading, source, trailing] = original.match(/^(\s*)([\s\S]*?)(\s*)$/);
    node.nodeValue = leading + translate(source) + trailing;
  }
  for (const [element, source] of textBindings) {
    if (!element.isConnected) { textBindings.delete(element); continue; }
    const text = translate(source);
    if (element.textContent !== text) element.textContent = text;
  }
  for (const [element, attributes] of attributeBindings) {
    if (!element.isConnected) { attributeBindings.delete(element); continue; }
    for (const [attribute, source] of attributes) element.setAttribute(attribute, translate(source));
  }
}
export function setLanguage(locale) {
  language = locale === 'en' ? 'en' : 'zh-CN';
  try { (typeof window === 'undefined' ? null : window.localStorage)?.setItem(STORAGE_KEY, language); } catch { /* Choice still works for this page. */ }
  if (typeof document !== 'undefined') {
    document.documentElement.lang = language;
    const selector = document.getElementById('language');
    if (selector) selector.value = language;
  }
  renderBindings();
}
export function initI18n() {
  if (initialized || typeof document === 'undefined') return;
  initialized = true;
  // Bind existing text nodes instead of replacing their parents: inputs, SVG icons,
  // screen readers and the preview keep exactly the same DOM/state.
  const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.parentElement?.closest('script, style, [data-no-i18n]')) continue;
    if (Object.hasOwn(zhCN, node.nodeValue.trim())) staticNodes.set(node, node.nodeValue);
  }
  for (const element of document.querySelectorAll('[aria-label], [title], [placeholder]')) {
    for (const attribute of ['aria-label','title','placeholder']) {
      if (element.hasAttribute(attribute)) setAttributeText(element, attribute, element.getAttribute(attribute));
    }
  }
  const selector = document.getElementById('language');
  selector?.addEventListener('change', () => setLanguage(selector.value));
  setLanguage(language);
}
