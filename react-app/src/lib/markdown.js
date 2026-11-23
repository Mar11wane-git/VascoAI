// Markdown rendering for agent output: highlight.js + DOMPurify sanitization.
// Model output can contain text copied from web pages, so it is always sanitized.
import { marked } from 'marked';
import hljs from 'highlight.js/lib/common';
import DOMPurify from 'dompurify';

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const renderer = new marked.Renderer();
renderer.code = function (codeObjOrStr, infostring) {
  let codeText = '';
  let lang = '';
  if (typeof codeObjOrStr === 'object' && codeObjOrStr !== null) {
    codeText = codeObjOrStr.text || '';
    lang = (codeObjOrStr.lang || '').trim().toLowerCase();
  } else {
    codeText = String(codeObjOrStr || '');
    lang = (infostring || '').trim().toLowerCase();
  }
  const validLang = lang && hljs.getLanguage(lang) ? lang : 'plaintext';
  const highlighted = hljs.highlight(codeText, { language: validLang }).value;
  const encoded = encodeURIComponent(codeText);
  const isPreviewable =
    ['html', 'xml', 'svg'].includes(lang) || codeText.includes('<!DOCTYPE html>') || codeText.includes('<html');

  return `
    <div class="code-block-wrapper">
      <div class="code-block-header">
        <span>${escapeHtml(lang || 'code')}</span>
        <div class="code-block-actions">
          ${isPreviewable ? `<button class="code-action-btn preview-btn" data-action="preview" data-code="${encoded}">▶ Aperçu Live</button>` : ''}
          <button class="code-action-btn" data-action="copy" data-code="${encoded}">📋 Copier</button>
        </div>
      </div>
      <pre><code class="hljs language-${validLang}">${highlighted}</code></pre>
    </div>`;
};
marked.setOptions({ renderer, breaks: true, gfm: true });

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
  if (node.tagName === 'IMG') {
    node.setAttribute('loading', 'lazy');
  }
});

export function renderMarkdown(text) {
  const html = marked.parse(text || '');
  return DOMPurify.sanitize(html, { ADD_ATTR: ['target', 'data-action', 'data-code'] });
}
