/* Brit Placement apply engine.
   Injected into a job / application page by the Chrome extension (background.js) and by
   the server's Playwright browser (placement/autoapply.py). Each step() call handles the
   page once: open the application (Easy Apply / Apply now), fill the visible step
   (profile, saved answers, then AI answers from the backend), attach the CV, press
   Next/Review/Submit and report what happened. The caller loops until a final state. */
(() => {
  if (window.__britEngine && window.__britEngine.version === 3) return;
  const state = window.__britState || (window.__britState = {signatures: {}, uploaded: {}});

  const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const text = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  const fire = (el, type) => el.dispatchEvent(new Event(type, {bubbles: true}));

  function shown(el) {
    if (!el || !el.isConnected) return false;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  }

  // Radios/checkboxes are often visually hidden behind a styled label.
  function usable(el) {
    if (!el || el.disabled) return false;
    if (shown(el)) return true;
    const type = String(el.type || '').toLowerCase();
    if (type === 'radio' || type === 'checkbox') {
      const label = (el.labels && el.labels[0]) || el.closest('label');
      return !!(label && shown(label)) || !!(el.parentElement && shown(el.parentElement));
    }
    return false;
  }

  // File inputs are usually hidden behind a visible "Upload" label or button.
  function fileUsable(input) {
    if (input.disabled) return false;
    if (shown(input)) return true;
    if (input.labels && [...input.labels].some(shown)) return true;
    const wrap = input.closest('label');
    if (wrap && shown(wrap)) return true;
    return !!input.parentElement && shown(input.parentElement);
  }
  const fileInputs = root => [...root.querySelectorAll('input[type="file"]')].filter(fileUsable);

  const outsideChrome = el => !el.closest('header, nav, footer, [role="search"], [role="banner"], [role="navigation"]');

  function rawLabel(el) {
    const parts = [];
    if (el.labels) for (const l of el.labels) parts.push(text(l));
    const by = el.getAttribute('aria-labelledby');
    if (by) for (const id of by.split(/\s+/)) { const node = document.getElementById(id); if (node) parts.push(text(node)); }
    if (!parts.join('').trim() && el.getAttribute('aria-label')) parts.push(el.getAttribute('aria-label'));
    if (!parts.join('').trim()) { const wrap = el.closest('label'); if (wrap) parts.push(text(wrap)); }
    let node = el;
    for (let i = 0; i < 4 && !parts.join('').trim(); i++) {
      node = node.parentElement;
      if (!node) break;
      const cand = node.querySelector('label, legend, [class*="label"], [class*="Label"], [class*="question"], h3, h4');
      if (cand && !cand.contains(el) && text(cand).length < 300) parts.push(text(cand));
    }
    if (!parts.join('').trim()) parts.push(el.getAttribute('placeholder') || el.name || el.id || '');
    return [...new Set(parts.map(x => x.replace(/\s+/g, ' ').trim()).filter(Boolean))].join(' ');
  }
  const labelOf = el => rawLabel(el).replace(/\*+/g, '').replace(/\(required\)/ig, '').trim().slice(0, 400);

  function optionText(input) {
    const label = (input.labels && input.labels[0]) || input.closest('label');
    return (text(label) || input.getAttribute('aria-label') || text(input.nextElementSibling) || input.value || '').slice(0, 200);
  }

  function groupLabel(inputs) {
    const first = inputs[0];
    const fieldset = first.closest('fieldset');
    if (fieldset) {
      const legend = fieldset.querySelector('legend');
      if (legend && text(legend)) return text(legend);
    }
    const group = first.closest('[role="radiogroup"], [role="group"]');
    if (group) {
      const by = group.getAttribute('aria-labelledby');
      const named = by && document.getElementById(by.split(/\s+/)[0]);
      if (named && text(named)) return text(named);
      if (group.getAttribute('aria-label')) return group.getAttribute('aria-label');
    }
    let node = first.parentElement;
    while (node && !inputs.every(i => node.contains(i))) node = node.parentElement;
    for (let i = 0; node && i < 3; i++) {
      let t = text(node);
      for (const option of inputs.map(optionText)) if (option) t = t.replace(option, '');
      t = t.replace(/\s+/g, ' ').trim();
      if (t.length > 2) return t.slice(0, 400);
      const prev = node.previousElementSibling;
      if (prev && text(prev) && text(prev).length < 300) return text(prev);
      node = node.parentElement;
    }
    return first.name || '';
  }

  function collect(root) {
    const fields = [];
    const groups = new Set();
    let index = 0;
    for (const el of root.querySelectorAll('input, textarea, select')) {
      const type = String(el.type || el.tagName).toLowerCase();
      if (['hidden', 'submit', 'button', 'reset', 'image', 'password', 'search', 'file'].includes(type)) continue;
      if (!usable(el) || !outsideChrome(el)) continue;
      // Validation shadows of custom widgets (react-select etc.) are aria-hidden and unfocusable.
      if (type !== 'radio' && type !== 'checkbox' && (el.getAttribute('aria-hidden') === 'true' || el.closest('[aria-hidden="true"]'))) continue;
      const required = el.required || el.getAttribute('aria-required') === 'true';
      if (type === 'radio' || type === 'checkbox') {
        const scope = el.closest('fieldset') || root;
        const siblings = el.name
          ? [...root.querySelectorAll(`input[type="${type}"][name="${CSS.escape(el.name)}"]`)]
          : [...scope.querySelectorAll(`input[type="${type}"]`)];
        const members = siblings.filter(usable);
        if (type === 'checkbox' && members.length < 2) {
          fields.push({key: 'f' + index++, kind: 'check1', el, label: labelOf(el), required: required || /\*/.test(rawLabel(el))});
          continue;
        }
        const id = type + ':' + (el.name || members.indexOf(el) + ':' + groupLabel(members));
        if (groups.has(id)) continue;
        groups.add(id);
        const label = groupLabel(members);
        fields.push({key: 'f' + index++, kind: type, els: members, label: label.replace(/\*+/g, '').trim(),
                     options: members.map(optionText), required: members.some(m => m.required || m.getAttribute('aria-required') === 'true') || /\*/.test(label)});
        continue;
      }
      const label = labelOf(el);
      const req = required || /\*/.test(rawLabel(el));
      if (el.tagName === 'SELECT') {
        const options = [...el.options].filter(o => o.value !== '' && !/^(select|please select|choose|--|none selected)/i.test(o.text.trim())).map(o => o.text.trim());
        fields.push({key: 'f' + index++, kind: 'select', el, label, options, required: req});
      } else if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list') {
        fields.push({key: 'f' + index++, kind: 'combobox', el, label, required: req});
      } else {
        const kind = el.tagName === 'TEXTAREA' ? 'textarea' : type === 'number' ? 'number' : type === 'date' ? 'date' : 'text';
        fields.push({key: 'f' + index++, kind, el, label, required: req, maxLength: el.maxLength > 0 ? el.maxLength : 0});
      }
    }
    return fields;
  }

  function placeholderSelected(select) {
    const option = select.options[select.selectedIndex];
    return !option || option.value === '' || /^(select|please select|choose|--)/i.test(option.text.trim());
  }

  // react-select style widgets show the chosen value in a sibling element, not in the input.
  function comboValue(el) {
    let node = el;
    for (let i = 0; i < 5 && node; i++) {
      node = node.parentElement;
      const chosen = node && node.querySelector('[class*="singleValue" i], [class*="single-value" i], [class*="multiValue" i], [class*="multi-value" i]');
      if (chosen && text(chosen)) return text(chosen);
    }
    return '';
  }

  function isFilled(field) {
    if (field.kind === 'radio' || field.kind === 'checkbox') return field.els.some(e => e.checked);
    if (field.kind === 'check1') return field.el.checked;
    if (field.kind === 'select') return !placeholderSelected(field.el);
    if (field.kind === 'combobox' && comboValue(field.el)) return true;
    return String(field.el.value || '').trim() !== '';
  }

  // Stable selector so the driver (Playwright / chrome.debugger) can send trusted input.
  function selectorFor(el) {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    if (!el.dataset.britK) el.dataset.britK = 'k' + Math.random().toString(36).slice(2, 9);
    return `[data-brit-k="${el.dataset.britK}"]`;
  }

  function isInvalid(field) {
    const els = field.els || [field.el];
    return els.some(e => e.getAttribute('aria-invalid') === 'true' || (e.validity && !e.validity.valid && e.required));
  }

  function fieldError(field) {
    const el = (field.els || [field.el])[0];
    const id = el.getAttribute('aria-describedby');
    const described = id && id.split(/\s+/).map(x => document.getElementById(x)).filter(Boolean).map(text).join(' ');
    return (described || '').slice(0, 200);
  }

  function profileValue(label, el, p) {
    const l = norm(label);
    const type = String((el && el.type) || '').toLowerCase();
    if (/country code|phone code|dial code|extension/.test(l)) return null;
    if (type === 'email' || /\be ?mail\b/.test(l)) return p.email;
    if (type === 'tel' || /\b(phone|mobile|telephone|contact number)\b/.test(l)) return p.phone;
    if (/\b(first|given|fore) ?names?\b/.test(l)) return p.first_name;
    if (/\b(last|family) ?name\b|\bsurname\b/.test(l)) return p.last_name;
    if (/\bmiddle name\b/.test(l)) return null;
    if (/^(full )?name$|\bfull name\b|\byour name\b|\blegal name\b|\bpreferred name\b/.test(l)) return p.name;
    if (/linkedin/.test(l)) return p.linkedin_url;
    if (/\b(website|portfolio|github|personal site|blog)\b/.test(l)) return p.portfolio_url;
    if (/\b(post ?code|zip|postal code)\b/.test(l)) return p.postcode;
    if (/\baddress\b/.test(l) && !/e ?mail/.test(l)) return p.address;
    if (/\b(city|town)\b/.test(l)) return p.city;
    if (/^(current )?location$|\byour location\b|\bwhere are you (based|located)\b/.test(l)) return p.location;
    if (/salary|compensation|pay expectation/.test(l) && p.salary_expectation) return p.salary_expectation;
    if (/notice period/.test(l) && p.notice_period) return p.notice_period;
    return null;
  }

  function bestOption(value, options) {
    const want = norm(value);
    if (!want) return -1;
    let i = options.findIndex(o => norm(o) === want);
    if (i < 0) i = options.findIndex(o => norm(o) && (norm(o).startsWith(want) || want.startsWith(norm(o))));
    if (i < 0) i = options.findIndex(o => norm(o) && (norm(o).includes(want) || want.includes(norm(o))));
    return i;
  }

  function setValue(el, value) {
    el.focus();
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    if (descriptor && descriptor.set) descriptor.set.call(el, value); else el.value = value;
    fire(el, 'input'); fire(el, 'change');
    el.dispatchEvent(new FocusEvent('blur', {bubbles: false}));
    fire(el, 'focusout');
  }

  function check(el, wanted = true) {
    if (el.checked === wanted) return;
    const label = (el.labels && el.labels[0]) || el.closest('label');
    (shown(el) ? el : label || el).click();
    if (el.checked !== wanted) { el.checked = wanted; fire(el, 'input'); fire(el, 'change'); }
  }

  // Custom dropdowns (react-select on Greenhouse, etc.) only render options when opened.
  const comboControl = el => el.closest('[class*="control" i]') || el.parentElement?.parentElement || el;

  async function comboOptions(el) {
    el.focus();
    comboControl(el).dispatchEvent(new MouseEvent('mousedown', {bubbles: true, button: 0}));
    el.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', code: 'ArrowDown', keyCode: 40, bubbles: true}));
    await sleep(350);
    const options = [...document.querySelectorAll('[role="option"]')].filter(shown).map(text).filter(Boolean).slice(0, 80);
    el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true}));
    el.blur();
    await sleep(100);
    return options.length > 1 && options.length < 80 && !options.some(o => /^(loading|no options)/i.test(o)) ? options : [];
  }

  async function setCombobox(el, value) {
    if (comboValue(el) && norm(comboValue(el)) === norm(value)) return true;
    setValue(el, '');
    el.focus();
    const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    descriptor.set.call(el, value);
    fire(el, 'input');
    if (el.getAttribute('aria-expanded') !== 'true') comboControl(el).dispatchEvent(new MouseEvent('mousedown', {bubbles: true, button: 0}));
    for (let i = 0; i < 8; i++) {
      await sleep(250);
      const options = [...document.querySelectorAll('[role="option"], [role="listbox"] li')].filter(shown);
      if (options.length) {
        const index = bestOption(value, options.map(text));
        const pick = options[index >= 0 ? index : 0];
        pick.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
        pick.click();
        await sleep(200);
        return true;
      }
    }
    el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    // Widgets that ignore synthetic events keep an empty value; the driver retries with trusted input.
    if (comboValue(el)) return true;
    if (el.getAttribute('aria-autocomplete') !== 'list' && String(el.value || '').trim() !== '') return true;
    setValue(el, '');
    return false;
  }

  async function setField(field, value) {
    if (value === null || value === undefined || value === '') return false;
    switch (field.kind) {
      case 'text': case 'textarea': case 'date': {
        const v = String(Array.isArray(value) ? value[0] : value);
        setValue(field.el, field.maxLength ? v.slice(0, field.maxLength) : v); return true;
      }
      case 'number': {
        const digits = String(value).replace(/,/g, '').match(/-?\d+(\.\d+)?/);
        if (!digits) return false;
        setValue(field.el, digits[0]); return true;
      }
      case 'select': {
        const options = [...field.el.options];
        const index = bestOption(Array.isArray(value) ? value[0] : value, options.map(o => o.text.trim()));
        if (index < 0) return false;
        field.el.value = options[index].value;
        options[index].selected = true;
        fire(field.el, 'input'); fire(field.el, 'change');
        return true;
      }
      case 'radio': {
        const index = bestOption(Array.isArray(value) ? value[0] : value, field.options);
        if (index < 0) return false;
        check(field.els[index]); return true;
      }
      case 'checkbox': {
        const values = Array.isArray(value) ? value : String(value).split('|');
        let any = false;
        for (const v of values) { const i = bestOption(v, field.options); if (i >= 0) { check(field.els[i]); any = true; } }
        return any;
      }
      case 'check1':
        if (/^(yes|true|1|agree|accept|checked|y)\b/i.test(String(value))) { check(field.el); return true; }
        return /^(no|false|0)\b/i.test(String(value));
      case 'combobox':
        return setCombobox(field.el, String(Array.isArray(value) ? value[0] : value));
      default:
        return false;
    }
  }

  function askBackend(payload) {
    if (typeof window.__britAsk === 'function') return window.__britAsk(payload);
    return new Promise((resolve, reject) => {
      try {
        chrome.runtime.sendMessage({type: 'brit-answers', payload}, response => {
          if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
          else resolve(response);
        });
      } catch (error) { reject(error); }
    });
  }

  const CONSENT = /agree|consent|terms|privacy|acknowledge|confirm|certify|accept|declare|understand|data protection|gdpr/i;

  async function fillFields(fields, ctx, retry) {
    const profile = ctx.profile || {};
    const saved = ctx.answers || {};
    const pending = [], trusted = [];
    let count = 0;
    for (const field of fields) {
      if (isFilled(field) && !(retry && isInvalid(field))) continue;
      if (field.kind === 'check1' && CONSENT.test(field.label)) { check(field.el); count++; continue; }
      let value = field.kind === 'check1' ? null : profileValue(field.label, field.el, profile);
      if (value === null || value === undefined || value === '') value = saved[norm(field.label)];
      if (value !== null && value !== undefined && value !== '' && !(retry && isInvalid(field))) {
        if (await setField(field, value)) { count++; continue; }
        if (field.kind === 'combobox' && ctx.trusted) { trusted.push({selector: selectorFor(field.el), value: String(value)}); continue; }
      }
      pending.push(field);
    }
    const unknown = [], needOptions = [];
    for (const field of pending) {
      if (field.kind !== 'combobox' || field.options) continue;
      const selector = selectorFor(field.el);
      const known = ctx.comboOptions && ctx.comboOptions[selector];
      field.options = known || await comboOptions(field.el);
      if (!field.options.length && ctx.trusted && !known) needOptions.push(selector);
    }
    if (needOptions.length) return {count, unknown, needOptions, trusted};
    if (pending.length) {
      let response = null;
      try {
        response = await askBackend({
          application_id: ctx.applicationId, learner_id: profile.learner_id, job: ctx.job,
          fields: pending.map(f => ({key: f.key, label: f.label, required: !!f.required, max_length: f.maxLength || 0,
            type: f.kind === 'check1' ? 'boolean' : f.kind === 'combobox' ? 'text' : f.kind,
            options: f.options || [], error: retry ? fieldError(f) || undefined : undefined}))
        });
      } catch (error) { response = null; }
      const answers = (response && response.answers) || {};
      for (const field of pending) {
        const value = answers[field.key];
        const ok = await setField(field, value);
        if (ok) count++;
        else if (field.kind === 'combobox' && ctx.trusted && value) trusted.push({selector: selectorFor(field.el), value: String(Array.isArray(value) ? value[0] : value)});
        else if (field.required) unknown.push(field.label);
      }
    }
    return {count, unknown, trusted, needOptions};
  }

  function uploadCV(root, cv) {
    if (!cv || !cv.content_base64) return false;
    const inputs = fileInputs(root);
    const score = i => {
      const l = norm(labelOf(i) + ' ' + (i.name || '') + ' ' + (i.id || '') + ' ' + (i.getAttribute('data-testid') || ''));
      if (/cover/.test(l)) return -1;
      if (/resume|cv|curriculum/.test(l)) return 2;
      return 1;
    };
    const target = inputs.filter(i => score(i) >= 0).sort((a, b) => score(b) - score(a))[0];
    if (!target) return false;
    const key = location.host + location.pathname + ':' + (target.name || target.id || 'file');
    if (state.uploaded[key] || (target.files && target.files.length)) return true;
    const bytes = Uint8Array.from(atob(cv.content_base64), c => c.charCodeAt(0));
    const file = new File([bytes], cv.filename || 'CV.pdf', {type: cv.mime_type || 'application/pdf'});
    const transfer = new DataTransfer();
    transfer.items.add(file);
    target.files = transfer.files;
    fire(target, 'input'); fire(target, 'change');
    state.uploaded[key] = true;
    return true;
  }

  const SUCCESS = /(your application (was|has been) (sent|submitted|received)|application (was |has been )?(submitted|sent|received)( successfully)?|thank(s| you) for (applying|your application)|we('ve| have) (successfully )?received your application|successfully applied|you have (successfully )?applied|application successful|application complete)/i;

  function successText() {
    const match = text(document.body).slice(0, 30000).match(SUCCESS);
    return match ? match[0] : null;
  }

  function alreadyApplied() {
    const host = location.hostname;
    if (/(^|\.)linkedin\.com$/.test(host)) {
      const top = document.querySelector('.job-details-jobs-unified-top-card__container--two-pane, .jobs-unified-top-card, .jobs-details__main-content, .jobs-search__job-details--wrapper');
      const match = text(top).slice(0, 3000).match(/Applied \d+ (second|minute|hour|day|week|month)s? ago|Application submitted/i);
      return match ? 'LinkedIn: ' + match[0] : null;
    }
    if (/(^|\.)indeed\.com$/.test(host) && !/apply\.indeed\.com$/.test(host)) {
      const match = text(document.querySelector('#jobsearch-ViewjobPaneWrapper, .jobsearch-JobComponent, main') || document.body).slice(0, 4000).match(/You applied (on|to this job)[^.]{0,40}|Applied on \w+ \d+/i);
      return match ? 'Indeed: ' + match[0] : null;
    }
    return null;
  }

  function challenge() {
    // Invisible reCAPTCHA (a 60px badge) is on most ATS forms and never blocks a normal visitor;
    // only a visible checkbox (~78px) or an open challenge window does.
    const frames = [...document.querySelectorAll('iframe')].filter(shown);
    const blocking = f => {
      const src = f.src || '', box = f.getBoundingClientRect();
      if (/size=invisible/.test(src)) return false;
      if (/recaptcha\/(api2|enterprise)\/bframe|hcaptcha\.com.*challenge|arkoselabs|funcaptcha/.test(src)) return box.height > 150;
      if (/recaptcha\/(api2|enterprise)\/anchor|hcaptcha\.com|challenges\.cloudflare\.com/.test(src)) return box.height >= 70 && box.width >= 250;
      return false;
    };
    if (frames.some(blocking)) return true;
    return /just a moment|verify you are human|are you a robot|security check|attention required/i.test(document.title);
  }

  function loginWall() {
    const url = location.href;
    if (/linkedin\.com\/(authwall|login|checkpoint|uas\/login|signup)/.test(url)) return true;
    if (/secure\.indeed\.com\/(auth|account)|indeed\.com\/account\/login/.test(url)) return true;
    if (/glassdoor\.[^/]+\/(profile\/login|member\/login|index\.htm#login)/.test(url)) return true;
    const passwords = [...document.querySelectorAll('input[type="password"]')].filter(shown);
    if (passwords.length) {
      const visible = [...document.querySelectorAll('input:not([type="hidden"]), textarea, select')].filter(shown);
      if (visible.length <= 5) return true;
    }
    return false;
  }

  const ATS_FRAME = /greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|smartrecruiters\.com|recruitee\.com|personio\.|teamtailor\.com|bamboohr\.com|jobvite\.com|icims\.com|myworkdayjobs\.com|applytojob\.com|breezy\.hr|pinpointhq\.com/;
  function atsFrame() {
    const frame = [...document.querySelectorAll('iframe')].find(f => shown(f) && ATS_FRAME.test(f.src || '') && f.getBoundingClientRect().height > 150);
    return frame ? frame.src : null;
  }

  function boardJobPage() {
    const host = location.hostname;
    return /(^|\.)linkedin\.com$/.test(host) || /glassdoor\./.test(host) ||
      (/(^|\.)indeed\.com$/.test(host) && !/(smartapply|apply)\.indeed\.com$/.test(host));
  }

  function fieldCount(root) {
    return [...root.querySelectorAll('input:not([type="hidden"]):not([type="search"]), textarea, select')].filter(e => usable(e) && outsideChrome(e)).length;
  }

  function findRoot() {
    const dialogs = [...document.querySelectorAll('[role="dialog"], .artdeco-modal, [aria-modal="true"]')]
      .filter(d => shown(d) && !/cookie|consent/i.test(text(d).slice(0, 160)));
    // Review steps have no inputs, only a Review/Submit button.
    const formDialog = dialogs.find(d => fieldCount(d) || fileInputs(d).length || pickButton(d));
    if (formDialog) return formDialog;
    if (boardJobPage()) {
      // LinkedIn Easy Apply between steps has a dialog with only buttons.
      const applyDialog = dialogs.find(d => /apply|application|review/i.test(text(d).slice(0, 300)));
      return applyDialog || null;
    }
    const forms = [...document.querySelectorAll('form')].filter(f => shown(f) && outsideChrome(f))
      .map(f => [f, fieldCount(f) + 3 * fileInputs(f).length]).sort((a, b) => b[1] - a[1]);
    if (forms.length && forms[0][1] > 0) return forms[0][0];
    return document.body;
  }

  const STRONG = /name|e ?mail|phone|resume|\bcv\b|cover|experience|salary|notice|authori[sz]|sponsor|visa|address|linkedin|why|eligib|right to work|postcode/i;
  function isApplicationForm(root, fields) {
    if (!root) return false;
    const files = fileInputs(root).length;
    if (root !== document.body && root.matches('[role="dialog"], .artdeco-modal, [aria-modal="true"]') && (fields.length || files || pickButton(root))) return true;
    if (files) return true;
    const strong = fields.filter(f => STRONG.test(f.label));
    return strong.length >= 2 || (fields.length >= 4 && strong.length >= 1);
  }

  const BUTTONS = {
    submit: /^(submit( my| your)? application|submit|send( my)? application|apply|apply now|complete( my)? application|finish|confirm and submit|send|submit and finish)$/i,
    review: /^(review( my| your)? application|review)$/i,
    next: /^(next|continue|continue to next step|save (and|&) continue|proceed|next step|go to next step|save and next|next page)$/i,
    never: /^(back|previous|cancel|dismiss|close|save( for later)?|discard|edit|remove|delete|upload|sign in|log in|apply with (linkedin|indeed|seek)|share|report)$/i,
  };

  function buttonLabel(button) {
    const aria = (button.getAttribute('aria-label') || '').trim();
    if (/submit application|review your application|continue to next step/i.test(aria)) return aria;
    return (text(button) || button.value || aria || '').replace(/[›»→>]/g, '').replace(/\s+/g, ' ').trim();
  }

  function pickButton(root) {
    const buttons = [...root.querySelectorAll('button, input[type="submit"], input[type="button"], a[role="button"], [role="button"]')]
      .filter(b => shown(b) && !b.disabled && b.getAttribute('aria-disabled') !== 'true' && outsideChrome(b))
      .map(b => ({el: b, label: buttonLabel(b)}));
    for (const kind of ['submit', 'review', 'next']) {
      const hit = buttons.find(b => BUTTONS[kind].test(b.label) && !BUTTONS.never.test(b.label));
      if (hit) return {kind, el: hit.el, label: hit.label};
    }
    return null;
  }

  const ENTRY = /^(easy apply|apply|apply now|apply for (this|the) (job|role|position|vacancy)|apply online|start (your |my )?application|apply on company (site|website)|apply on employer site|quick apply|i'?m interested|apply with (your )?(cv|resume)|continue to application|continue applying|apply for job)$/i;
  function findEntry() {
    const candidates = [...document.querySelectorAll('button, a, [role="button"], input[type="submit"]')]
      .filter(el => shown(el) && !el.disabled && outsideChrome(el))
      .map(el => ({el, label: (text(el) || el.value || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim(), aria: el.getAttribute('aria-label') || ''}));
    return (candidates.find(c => /^easy apply/i.test(c.label) || /^easy apply to/i.test(c.aria)) ||
            candidates.find(c => ENTRY.test(c.label)) ||
            candidates.find(c => /^apply to .+ on company website$/i.test(c.aria)) || {}).el || null;
  }

  function dismissCookies() {
    const banner = [...document.querySelectorAll('[id*="cookie" i], [class*="cookie" i], [id*="consent" i], [class*="consent" i], #onetrust-banner-sdk, [id*="cmp" i]')].find(shown);
    if (!banner) return;
    const button = [...banner.querySelectorAll('button, a[role="button"]')].find(b => shown(b) && /^(accept( all)?( cookies)?|allow all( cookies)?|i agree|agree|got it|ok|accept and continue)$/i.test(text(b)));
    if (button) button.click();
  }

  function settle(max = 2500) {
    return new Promise(resolve => {
      let timer = null;
      const done = () => { observer.disconnect(); resolve(); };
      const observer = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(done, 600); });
      observer.observe(document.body, {subtree: true, childList: true});
      timer = setTimeout(done, 700);
      setTimeout(done, max);
    });
  }

  function errorsText(root) {
    return [...root.querySelectorAll('[role="alert"], .artdeco-inline-feedback--error, [class*="error" i], .invalid-feedback')]
      .filter(shown).map(text).filter(Boolean).join(' | ').slice(0, 300);
  }

  async function step(ctx) {
    ctx = ctx || {};
    const out = (stateName, extra = {}) => ({state: stateName, url: location.href, ...extra});
    dismissCookies();
    if (ctx.afterSubmit) { const ok = successText(); if (ok) return out('submitted', {evidence: ok}); }
    const already = alreadyApplied();
    if (already) return out('already_applied', {evidence: already});
    if (challenge()) return out('blocked', {reason: 'CAPTCHA / human verification on ' + location.hostname});
    if (loginWall()) return out('login_required', {reason: 'Login required on ' + location.hostname});

    const root = findRoot();
    const fields = root ? collect(root) : [];
    if (!isApplicationForm(root, fields)) {
      const frame = atsFrame();
      if (frame) return out('external', {url: frame});
      const entry = findEntry();
      if (entry) {
        const href = entry.tagName === 'A' ? entry.href : '';
        if (/^https:/.test(href) && new URL(href).host !== location.host) return out('external', {url: href});
        entry.click();
        await settle(3000);
        return out('continue', {clicked: 'entry'});
      }
      // A submit that navigated away loses afterSubmit; a form-less page thanking the applicant is the receipt.
      const receipt = successText();
      if (receipt) return out('submitted', {evidence: receipt});
      if (ctx.afterSubmit) return out('no_form', {reason: 'Submitted but no confirmation text found'});
      return out('no_form', {reason: 'No application form or apply button on ' + location.hostname});
    }

    const signature = fields.map(f => f.label).join('|').slice(0, 600) + '#' + location.pathname;
    const seen = state.signatures[signature] = (state.signatures[signature] || 0) + 1;
    const filled = await fillFields(fields, ctx, seen > 1);
    if (filled.needOptions.length) {
      state.signatures[signature]--;
      return out('need_options', {selectors: filled.needOptions});
    }
    uploadCV(root, ctx.cv);
    if (filled.trusted.length) {
      state.signatures[signature]--;
      return out('trusted_fill', {fills: filled.trusted, filled: filled.count});
    }
    if (ctx.fillOnly) return out('filled', {filled: filled.count, unknown: filled.unknown});
    if (seen > 3) return out('needs_input', {reason: 'Form did not advance. ' + errorsText(root), unknown: filled.unknown});
    const button = pickButton(root) || (root !== document.body ? pickButton(document.body) : null);
    if (!button) return out('needs_input', {reason: 'No Next/Submit button found', unknown: filled.unknown});
    if (button.kind === 'submit' && ctx.submit === false) return out('ready', {reason: 'Ready to submit', filled: filled.count});
    button.el.click();
    await settle(button.kind === 'submit' ? 4000 : 2500);
    if (button.kind === 'submit') {
      const ok = successText();
      if (ok) return out('submitted', {evidence: ok});
    }
    return out('continue', {clicked: button.kind, filled: filled.count, unknown: filled.unknown});
  }

  function jobInfo() {
    const pick = selectors => { for (const s of selectors) { const v = text(document.querySelector(s)); if (v) return v.slice(0, 250); } return ''; };
    return {
      title: pick(['.job-details-jobs-unified-top-card__job-title', '.jobs-unified-top-card__job-title', '[data-testid="jobsearch-JobInfoHeader-title"]', '[data-test="job-title"]', 'h1']) || document.title.split(/ [|\-–] /)[0],
      company: pick(['.job-details-jobs-unified-top-card__company-name', '.jobs-unified-top-card__company-name', '[data-testid="inlineHeader-companyName"]', '[data-company-name]', '[data-test="employer-name"]', '[class*="company" i]']) || location.hostname,
      location: pick(['.job-details-jobs-unified-top-card__primary-description-container', '[data-testid="inlineHeader-companyLocation"]', '[data-test="location"]', '[class*="location" i]']),
      description: text(document.querySelector('#job-details, .jobs-description, #jobDescriptionText, [class*="description" i], main') || document.body).slice(0, 8000),
      url: location.href.split('#')[0],
    };
  }

  const LISTING = {
    linkedin: /linkedin\.com\/jobs\/view\/(\d+)/, indeed: /indeed\.com\/.*[?&]jk=([a-f0-9]+)|indeed\.com\/viewjob/, glassdoor: /glassdoor\.[a-z.]+\/job-listing\//,
    reed: /reed\.co\.uk\/jobs\/[^/?#]+\/(\d+)/, totaljobs: /totaljobs\.com\/job\//, cwjobs: /cwjobs\.co\.uk\/job\//,
    jobsite: /jobsite\.co\.uk\/job\//, 'cv-library': /cv-library\.co\.uk\/job\/(\d+)/, guardian: /jobs\.theguardian\.com\/job\/(\d+)/,
    nhs: /jobs\.nhs\.uk\/candidate\/jobadvert\//, civilservice: /civilservicejobs\.service\.gov\.uk\/.*jcode=/,
    otta: /welcometothejungle\.com\/.*\/jobs\//, escapethecity: /escapethecity\.org\/opportunity\//, hays: /hays\.co\.uk\/job-detail\//,
  };

  function canonical(board, href) {
    const url = new URL(href, location.href);
    if (board === 'indeed') { const jk = url.searchParams.get('jk') || url.searchParams.get('vjk'); return jk ? `https://uk.indeed.com/viewjob?jk=${jk}` : url.href; }
    if (board === 'linkedin') { const id = (url.pathname.match(/\/jobs\/view\/(?:[^/]*?-)?(\d+)/) || [])[1]; return id ? `https://www.linkedin.com/jobs/view/${id}/` : url.href; }
    if (board === 'civilservice') return url.href;
    url.hash = ''; url.search = '';
    return url.href;
  }

  function harvest(hint) {
    const jobs = new Map();
    const cardSel = 'li, article, [data-testid*="job" i], [class*="job-card" i], [class*="jobcard" i], [class*="result" i], [data-jk]';
    for (const a of document.querySelectorAll('a[href]')) {
      const board = Object.keys(LISTING).find(b => LISTING[b].test(a.href)) || null;
      if (!board || (hint && hint !== board && !LISTING[hint]?.test(a.href))) continue;
      const card = a.closest(cardSel) || a.parentElement;
      const title = (a.getAttribute('aria-label') || text(a.querySelector('h2, h3, span[title], strong')) || text(a)).replace(/\s+with verification$/i, '').trim();
      if (title.length < 3 || title.length > 200 || /^(apply|save|view|more|show)/i.test(title)) continue;
      const url = canonical(board, a.href);
      if (jobs.has(url)) continue;
      const company = text(card && card.querySelector('[data-testid*="company" i], [class*="company" i], [class*="employer" i], [class*="recruiter" i], [data-at*="company" i], h4, .base-search-card__subtitle, .artdeco-entity-lockup__subtitle'));
      const where = text(card && card.querySelector('[data-testid*="location" i], [class*="location" i], [data-at*="location" i], .job-search-card__location'));
      const cardText = text(card).slice(0, 600);
      jobs.set(url, {board, url, title: title.slice(0, 200), company: company.slice(0, 150), location: where.slice(0, 150),
                     easy_apply: /easy apply|quick apply|easily apply|apply with indeed/i.test(cardText),
                     salary: (cardText.match(/£[\d,.]+k?(\s*-\s*£[\d,.]+k?)?( per (annum|year|hour|day))?/i) || [''])[0]});
    }
    return [...jobs.values()].slice(0, 200);
  }

  window.__britEngine = {version: 3, step, jobInfo, harvest};
})();
