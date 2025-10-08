'use strict';

const API_BASE = 'http://localhost:3000';
const CONFIG_REFRESH_INTERVAL_MS = 15000;
const RESCAN_INTERVAL_MS = 10000;
const RECENT_BUFFER_LIMIT = 50;
const TEXTUAL_CONDITIONS = new Set([
  'element_text',
  'text_equals',
  'text_differs',
  'text_contains',
  'text_not_contains',
  'text_length_gt',
  'text_length_lt'
]);
const LENGTH_CONDITIONS = new Set(['text_length_gt', 'text_length_lt']);

let rawRules = [];
let preparedRules = [];
let activeRules = [];
let ignoredApps = new Set();
let rulesSignature = '';
let lastTitle = document.title || '';

const recentKeys = [];
const recentKeySet = new Set();
const selectorErrorCache = new Set();
const ATTRIBUTE_NAMES_OF_INTEREST = new Set([
  'id',
  'class',
  'name',
  'role',
  'type',
  'value',
  'title',
  'placeholder',
  'disabled',
  'hidden',
  'aria-label',
  'aria-live',
  'aria-expanded',
  'aria-selected',
  'aria-pressed',
  'aria-current',
  'aria-disabled',
  'data-state',
  'data-status',
  'data-testid',
  'data-test',
  'data-qa',
  'data-automation-id'
]);
const SUMMARY_ATTRIBUTE_PRIORITY = [
  'data-state',
  'data-status',
  'aria-label',
  'aria-live',
  'aria-pressed',
  'aria-expanded',
  'aria-selected',
  'value',
  'class'
];
const DATA_ATTRIBUTE_CAPTURE_LIMIT = 6;
let ruleElementState = new Map();

const selectionState = {
  active: false,
  overlay: null,
  highlight: null,
  infoBox: null,
  lastTarget: null
};
let toastElement = null;

function rememberNotification(key) {
  if (recentKeySet.has(key)) {
    return false;
  }
  recentKeySet.add(key);
  recentKeys.push(key);
  if (recentKeys.length > RECENT_BUFFER_LIMIT) {
    const oldest = recentKeys.shift();
    if (oldest !== undefined) {
      recentKeySet.delete(oldest);
    }
  }
  return true;
}

function normalizeText(text) {
  if (!text) {
    return '';
  }
  return text.replace(/\s+/g, ' ').trim().slice(0, 200);
}

function truncate(value, max = 160) {
  if (value == null) {
    return '';
  }
  const str = String(value);
  if (str.length <= max) {
    return str;
  }
  return `${str.slice(0, max - 3)}...`;
}

function shortHash(input) {
  if (!input) {
    return '';
  }
  let hash = 0;
  for (let index = 0; index < input.length; index += 1) {
    hash = (hash << 5) - hash + input.charCodeAt(index);
    hash |= 0;
  }
  return hash.toString(16);
}

function getRelevantAttributeEntries(element) {
  if (!element || typeof element.getAttributeNames !== 'function') {
    return [];
  }
  const entries = [];
  let capturedDataAttributes = 0;
  for (const name of element.getAttributeNames()) {
    if (!name) {
      continue;
    }
    const lowerName = name.toLowerCase();
    const isData = lowerName.startsWith('data-');
    const isAria = lowerName.startsWith('aria-');
    if (
      !ATTRIBUTE_NAMES_OF_INTEREST.has(lowerName) &&
      !isData &&
      !isAria
    ) {
      continue;
    }
    if (isData) {
      capturedDataAttributes += 1;
      if (capturedDataAttributes > DATA_ATTRIBUTE_CAPTURE_LIMIT) {
        continue;
      }
    }
    const value = element.getAttribute(name);
    if (value == null || value === '') {
      continue;
    }
    entries.push([lowerName, truncate(value)]);
  }
  entries.sort((a, b) => a[0].localeCompare(b[0]));
  return entries;
}

function buildRuleState(rule, element, elementText) {
  const summaryParts = [];
  const attributes = {};
  const attributeEntries = getRelevantAttributeEntries(element);
  for (const [attrName, attrValue] of attributeEntries) {
    attributes[attrName] = attrValue;
  }

  const tagName = element?.tagName ? element.tagName.toLowerCase() : '';
  if (!attributes.class && element?.className) {
    attributes.class = truncate(element.className);
  }

  let inputValue = null;
  let checked = null;
  if (
    element instanceof HTMLInputElement ||
    element instanceof HTMLTextAreaElement ||
    element instanceof HTMLSelectElement
  ) {
    inputValue = truncate(element.value);
  }
  if (
    element instanceof HTMLInputElement &&
    (element.type === 'checkbox' || element.type === 'radio')
  ) {
    checked = Boolean(element.checked);
  }

  if (elementText) {
    summaryParts.push(`texto="${elementText}"`);
  }
  if (inputValue && (!elementText || inputValue !== elementText)) {
    summaryParts.push(`valor="${inputValue}"`);
  }

  for (const attrName of SUMMARY_ATTRIBUTE_PRIORITY) {
    if (summaryParts.length >= 3) {
      break;
    }
    if (attrName === 'value') {
      continue;
    }
    if (attrName === 'class' && attributes.class) {
      const classSummary = attributes.class
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 3)
        .join(' ');
      if (classSummary) {
        summaryParts.push(`classes="${classSummary}"`);
      }
      continue;
    }
    const attrValue = attributes[attrName];
    if (attrValue) {
      summaryParts.push(`${attrName}="${attrValue}"`);
    }
  }

  if (!summaryParts.length && attributes.class) {
    const classSummary = attributes.class
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 3)
      .join(' ');
    if (classSummary) {
      summaryParts.push(`classes="${classSummary}"`);
    }
  }

  const state = {
    tag: tagName,
    text: elementText || '',
    value: inputValue,
    checked,
    attributes
  };

  let signature = null;
  try {
    signature = JSON.stringify(state);
  } catch (error) {
    signature = null;
  }

  return {
    signature,
    summaryParts,
    state
  };
}

function shouldEvaluate(rule, element, signature) {
  if (!signature) {
    return true;
  }
  let elementState = ruleElementState.get(rule.id);
  if (!elementState) {
    elementState = new WeakMap();
    ruleElementState.set(rule.id, elementState);
  }
  const previousSignature = elementState.get(element);
  if (previousSignature === signature) {
    return false;
  }
  elementState.set(element, signature);
  return true;
}

function refreshRuleStateCache(rules) {
  const nextState = new Map();
  for (const rule of rules) {
    if (ruleElementState.has(rule.id)) {
      nextState.set(rule.id, ruleElementState.get(rule.id));
    } else {
      nextState.set(rule.id, new WeakMap());
    }
  }
  ruleElementState = nextState;
}

async function notifyServer(payload) {
  try {
    const response = await fetch(`${API_BASE}/notify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!response.ok) {
      throw new Error(`Status ${response.status}`);
    }
  } catch (error) {
    console.error('[notify-watcher] Erro ao enviar para o servidor local:', error);
  }
}

function sendTitleChangeNotification(newTitle) {
  if (ignoredApps.has('Browser')) {
    return;
  }
  const summary = `Título alterado para: ${newTitle}`;
  const key = `__title__|${summary}`;
  if (!rememberNotification(key)) {
    return;
  }
  console.log(`[notify-watcher] Mudança de título detectada: ${newTitle}`);
  notifyServer({ app: 'Browser', text: summary });
}

function getElementText(element) {
  if (!element) {
    return '';
  }
  const text = element.innerText || element.textContent || '';
  return normalizeText(text);
}

function triggerNotification(rule, details) {
  const appName = rule.appName || 'WebApp';
  if (ignoredApps.has(appName)) {
    return;
  }
  let summary = '';
  let keySuffix = '';
  if (typeof details === 'string') {
    summary = normalizeText(details);
    keySuffix = summary;
  } else if (details && typeof details === 'object') {
    summary = normalizeText(details.summary || details.text || details.message || '');
    keySuffix = details.key ? String(details.key) : summary;
  }
  if (!summary) {
    summary = `Regra '${appName}' acionada`;
    keySuffix = summary;
  }
  const key = `${rule.id}|${keySuffix}`;
  if (!rememberNotification(key)) {
    return;
  }
  console.log(`[notify-watcher] Regra acionada (${appName}): ${summary}`);
  dispatchNotification(rule, summary).catch(error => {
    console.error('[notify-watcher] Falha ao despachar notificação:', error);
  });
}

async function dispatchNotification(rule, summary) {
  const payload = {
    app: rule.appName || 'WebApp',
    text: summary,
    rule: {
      id: rule.id,
      name: rule.name,
      condition: rule.condition,
      selector: rule.displaySelector,
      url_contains: rule.url_contains
    }
  };

  if (document.visibilityState === 'visible' && chrome?.runtime?.sendMessage) {
    try {
      const response = await new Promise(resolve => {
        try {
          chrome.runtime.sendMessage({ type: 'capture_screenshot' }, resolve);
        } catch (err) {
          resolve({ error: err?.message || String(err) });
        }
      });
      if (response && response.image) {
        payload.screenshot = response.image;
      }
    } catch (error) {
      console.warn('[notify-watcher] Falha ao capturar screenshot:', error);
    }
  }

  await notifyServer(payload);
}

function sanitizeRule(rawRule) {
  if (!rawRule || typeof rawRule !== 'object') {
    return null;
  }
  const name = typeof rawRule.name === 'string' ? rawRule.name.trim() : '';
  const urlContainsRaw = typeof rawRule.url_contains === 'string' ? rawRule.url_contains : '';
  const urlContains = urlContainsRaw.trim();
  const pageUrl = typeof rawRule.page_url === 'string' ? rawRule.page_url : '';
  const typeRaw = typeof rawRule.type === 'string' ? rawRule.type.toLowerCase() : 'element';
  const type = typeRaw === 'element_text' ? 'element_text' : 'element';
  const selectorRaw = typeof rawRule.selector === 'string' ? rawRule.selector : '';
  const selector = selectorRaw.trim();
  const cssSelectorRaw = typeof rawRule.css_selector === 'string' ? rawRule.css_selector : (type === 'element' ? selector : '');
  const cssSelector = cssSelectorRaw.trim();
  const conditionRaw = typeof rawRule.condition === 'string' ? rawRule.condition.toLowerCase() : type;
  const condition = TEXTUAL_CONDITIONS.has(conditionRaw) || conditionRaw === 'element' ? conditionRaw : (type === 'element' ? 'element' : 'element_text');
  const baselineText = typeof rawRule.baseline_text === 'string' ? rawRule.baseline_text : '';
  const textSnapshot = typeof rawRule.text_snapshot === 'string' ? rawRule.text_snapshot : baselineText;
  const lengthValue = rawRule.length_threshold != null ? Number(rawRule.length_threshold) : null;
  const lengthThreshold = Number.isFinite(lengthValue) ? lengthValue : null;
  const source = typeof rawRule.source === 'string' ? rawRule.source : 'manual';
  const capturedAt = rawRule.captured_at != null ? rawRule.captured_at : null;
  const metadata = typeof rawRule.metadata === 'object' && rawRule.metadata ? rawRule.metadata : undefined;

  if (type === 'element' && !cssSelector) {
    return null;
  }
  if (condition === 'element_text' && !selector && !baselineText && !textSnapshot) {
    return null;
  }
  if (TEXTUAL_CONDITIONS.has(condition) && condition !== 'element_text') {
    if (condition === 'text_length_gt' || condition === 'text_length_lt') {
      if (!Number.isFinite(lengthThreshold)) {
        return null;
      }
    } else if (!baselineText && !selector && !textSnapshot) {
      return null;
    }
  }

  return {
    name: name || 'Regra',
    url_contains: urlContains,
    page_url: pageUrl,
    type,
    selector,
    css_selector: cssSelector,
    condition,
    baseline_text: baselineText,
    text_snapshot: textSnapshot,
    length_threshold: lengthThreshold,
    source,
    captured_at: capturedAt,
    metadata
  };
}

function prepareRule(rule, index) {
  const condition = rule.condition || rule.type || 'element';
  const cssSelector = typeof rule.css_selector === 'string' ? rule.css_selector : '';
  const textPattern = typeof rule.selector === 'string' ? rule.selector : '';
  const baselineText = typeof rule.baseline_text === 'string' ? rule.baseline_text : '';
  const textSnapshot = typeof rule.text_snapshot === 'string' && rule.text_snapshot ? rule.text_snapshot : baselineText;
  const lengthThreshold = Number.isFinite(rule.length_threshold) ? rule.length_threshold : null;
  const requiresText = TEXTUAL_CONDITIONS.has(condition);
  const id = `${rule.name || 'Regra'}|${condition}|${cssSelector || textPattern || index}`;
  const displaySelector = cssSelector || textPattern || '[sem seletor]';
  const fallbackCandidates = [];
  const meta = rule.metadata || {};
  const tagName = typeof meta.tag === 'string' ? meta.tag.toLowerCase() : '';
  if (meta.id) {
    fallbackCandidates.push(`#${escapeCss(meta.id)}`);
  }
  if (Array.isArray(meta.classes) && meta.classes.length) {
    const classSelector = meta.classes
      .slice(0, 3)
      .map(cls => `.${escapeCss(cls)}`)
      .join('');
    if (classSelector) {
      fallbackCandidates.push(tagName ? `${tagName}${classSelector}` : classSelector);
    }
  }
  if (meta.attributes && typeof meta.attributes === 'object') {
    const attributeEntries = Object.entries(meta.attributes)
      .filter(([attrName, attrValue]) => typeof attrName === 'string' && typeof attrValue === 'string' && attrValue.length)
      .slice(0, 6);
    for (const [attrName, attrValue] of attributeEntries) {
      const lowerAttr = attrName.toLowerCase();
      if (lowerAttr === 'id' || lowerAttr === 'class' || lowerAttr === 'value') {
        continue;
      }
      let selectorPart = '';
      if (attrValue === 'true' || attrValue === 'false') {
        selectorPart = `[${escapeCss(lowerAttr)}="${attrValue}"]`;
      } else if (lowerAttr === 'disabled' || lowerAttr === 'hidden') {
        selectorPart = `[${escapeCss(lowerAttr)}]`;
      } else {
        selectorPart = `[${escapeCss(lowerAttr)}="${escapeCss(attrValue)}"]`;
      }
      if (selectorPart) {
        fallbackCandidates.push(tagName ? `${tagName}${selectorPart}` : selectorPart);
      }
    }
  }
  const fallbackSelectors = Array.from(new Set(fallbackCandidates.filter(Boolean))).filter(sel => sel !== cssSelector);
  return {
    ...rule,
    appName: rule.name || 'WebApp',
    condition,
    cssSelector,
    textPattern,
    baselineText,
    textSnapshot,
    lengthThreshold,
    requiresText,
    id,
    displaySelector,
    fallbackSelectors
  };
}

function ruleMatchesCurrentUrl(rule) {
  if (!rule.url_contains) {
    return true;
  }
  return window.location.href.includes(rule.url_contains);
}

function updateActiveRules() {
  activeRules = preparedRules.filter(ruleMatchesCurrentUrl);
}

function applyConfig(data, initialScan) {
  const incomingRules = Array.isArray(data?.rules) ? data.rules : Array.isArray(data) ? data : [];
  const sanitized = incomingRules.map(sanitizeRule).filter(Boolean);
  const incomingIgnored = Array.isArray(data?.ignored_apps) ? data.ignored_apps : [];
  const ignored = incomingIgnored.map(item => String(item).trim()).filter(Boolean);

  const nextSignature = JSON.stringify({ rules: sanitized, ignored });
  const changed = nextSignature !== rulesSignature;
  rulesSignature = nextSignature;

  rawRules = sanitized;
  preparedRules = sanitized.map(prepareRule);
  ignoredApps = new Set(ignored);
  selectorErrorCache.clear();
  if (initialScan || changed) {
    refreshRuleStateCache(preparedRules);
  }
  updateActiveRules();

  if (initialScan || changed) {
    console.log(`[notify-watcher] Config carregada: ${preparedRules.length} regras (${activeRules.length} aplicáveis nesta página)`);
    scanDocument();
    rescanAllRules();
  }
}

async function loadConfig(initialScan = false) {
  try {
    const response = await fetch(`${API_BASE}/config`, { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`Status ${response.status}`);
    }
    const data = await response.json();
    applyConfig(data, initialScan);
  } catch (error) {
    console.error('[notify-watcher] Erro ao carregar config:', error);
  }
}

function evaluateCondition(rule, element, elementText, state) {
  const baseline = rule.baselineText || '';
  const pattern = baseline || rule.textPattern || '';
  const signature = state?.signature || '';
  const signatureKey = signature ? shortHash(signature) : '';
  const summaryParts = Array.isArray(state?.summaryParts) ? state.summaryParts.slice(0, 3) : [];
  switch (rule.condition) {
    case 'element':
      {
        const extras = summaryParts.length ? ` | ${summaryParts.join(' | ')}` : '';
        const keySuffix = signatureKey ? `element|${signatureKey}` : 'element';
        triggerNotification(rule, {
          summary: `Elemento detectado (${rule.displaySelector})${extras}`,
          key: `${rule.id}|${keySuffix}`
        });
      }
      break;
    case 'element_text':
      if (elementText && rule.textPattern && elementText.includes(rule.textPattern)) {
        triggerNotification(rule, {
          summary: `Texto encontrado: ${rule.textPattern}`,
          key: `${rule.id}|element_text|${signatureKey || elementText}`
        });
      }
      break;
    case 'text_equals':
      if (elementText && baseline && elementText === baseline) {
        triggerNotification(rule, {
          summary: `Texto corresponde: ${baseline}`,
          key: `${rule.id}|equals|${elementText}`
        });
      }
      break;
    case 'text_differs':
      if (elementText && baseline && elementText !== baseline) {
        triggerNotification(rule, {
          summary: `Texto alterado: ${elementText}`,
          key: `${rule.id}|${elementText}`
        });
      }
      break;
    case 'text_contains':
      if (elementText && pattern && elementText.includes(pattern)) {
        triggerNotification(rule, {
          summary: `Texto contém "${pattern}"`,
          key: `${rule.id}|contains|${elementText}`
        });
      }
      break;
    case 'text_not_contains':
      if (pattern && (!elementText || !elementText.includes(pattern))) {
        triggerNotification(rule, {
          summary: `Texto não contém "${pattern}"`,
          key: `${rule.id}|notcontains|${signatureKey || elementText}`
        });
      }
      break;
    case 'text_length_gt':
      if (rule.lengthThreshold != null && elementText.length > rule.lengthThreshold) {
        triggerNotification(rule, {
          summary: `Texto com ${elementText.length} caracteres (limite ${rule.lengthThreshold})`,
          key: `${rule.id}|len>${rule.lengthThreshold}|${elementText.length}`
        });
      }
      break;
    case 'text_length_lt':
      if (rule.lengthThreshold != null && elementText.length < rule.lengthThreshold) {
        triggerNotification(rule, {
          summary: `Texto com ${elementText.length} caracteres (limite ${rule.lengthThreshold})`,
          key: `${rule.id}|len<${rule.lengthThreshold}|${elementText.length}`
        });
      }
      break;
    default:
      break;
  }
}

function matchesPrimarySelector(rule, element) {
  if (!rule.cssSelector) {
    return true;
  }
  try {
    return Boolean(element.matches && element.matches(rule.cssSelector));
  } catch (error) {
    if (!selectorErrorCache.has(rule.cssSelector)) {
      selectorErrorCache.add(rule.cssSelector);
      console.error('[notify-watcher] Seletor inválido:', rule.cssSelector, error);
    }
    return false;
  }
}

function matchesFallbackSelector(rule, element) {
  if (!rule.fallbackSelectors || !rule.fallbackSelectors.length) {
    return false;
  }
  for (const selector of rule.fallbackSelectors) {
    try {
      if (element.matches && element.matches(selector)) {
        return true;
      }
    } catch (error) {
      if (!selectorErrorCache.has(selector)) {
        selectorErrorCache.add(selector);
        console.error('[notify-watcher] Seletor inválido:', selector, error);
      }
    }
  }
  return false;
}

function evaluateRuleOnElement(rule, element, { allowFallback = false } = {}) {
  if (!(element instanceof Element)) {
    return;
  }
  if (!matchesPrimarySelector(rule, element)) {
    if (!(allowFallback && matchesFallbackSelector(rule, element))) {
      return;
    }
  }
  let elementText = '';
  const needsText = rule.requiresText || rule.condition === 'element';
  if (needsText) {
    elementText = getElementText(element);
  }
  if (allowFallback && elementText && rule.baselineText) {
    const baselineLen = rule.baselineText.length || 1;
    const maxAllowed = Math.max(baselineLen * 4, 250);
    if (elementText.length > maxAllowed) {
      return;
    }
  }
  const state = buildRuleState(rule, element, elementText);
  if (!shouldEvaluate(rule, element, state.signature)) {
    return;
  }
  evaluateCondition(rule, element, elementText, state);
}

function evaluateElement(element) {
  if (!(element instanceof Element)) {
    return;
  }
  for (const rule of activeRules) {
    evaluateRuleOnElement(rule, element);
  }
}

function processNode(node) {
  if (!(node instanceof Element)) {
    return;
  }
  evaluateElement(node);
  const descendants = node.querySelectorAll('*');
  for (const descendant of descendants) {
    evaluateElement(descendant);
  }
}

function scanDocument() {
  if (!document.body) {
    return;
  }
  processNode(document.body);
}

function rescanAllRules() {
  if (!document.body || !activeRules.length) {
    return;
  }
  for (const rule of activeRules) {
    let matchedPrimary = false;
    if (rule.cssSelector) {
      try {
        const primaryElements = document.querySelectorAll(rule.cssSelector);
        if (primaryElements.length) {
          matchedPrimary = true;
          primaryElements.forEach(element => evaluateRuleOnElement(rule, element));
        }
      } catch (error) {
        if (!selectorErrorCache.has(rule.cssSelector)) {
          selectorErrorCache.add(rule.cssSelector);
          console.error('[notify-watcher] Seletor inválido durante varredura:', rule.cssSelector, error);
        }
      }
    }
    if (!matchedPrimary && rule.fallbackSelectors && rule.fallbackSelectors.length) {
      const seen = new Set();
      for (const selector of rule.fallbackSelectors) {
        let elements;
        try {
          elements = document.querySelectorAll(selector);
        } catch (error) {
          if (!selectorErrorCache.has(selector)) {
            selectorErrorCache.add(selector);
            console.error('[notify-watcher] Seletor inválido durante varredura:', selector, error);
          }
          continue;
        }
        elements.forEach(element => {
          if (!seen.has(element)) {
            seen.add(element);
            evaluateRuleOnElement(rule, element, { allowFallback: true });
          }
        });
        if (seen.size > 0) {
          break;
        }
      }
    }
  }
}

function checkTitleChange() {
  if (document.title !== lastTitle) {
    lastTitle = document.title;
    sendTitleChangeNotification(lastTitle);
  }
}

function observeDom() {
  const observer = new MutationObserver(mutations => {
    checkTitleChange();
    for (const mutation of mutations) {
      if (mutation.type === 'childList') {
        mutation.addedNodes.forEach(processNode);
      } else if (mutation.type === 'characterData' && mutation.target && mutation.target.parentElement) {
        processNode(mutation.target.parentElement);
      } else if (mutation.type === 'attributes' && mutation.target instanceof Element) {
        evaluateElement(mutation.target);
      }
    }
  });

  const startObserving = () => {
    if (!document.body) {
      setTimeout(startObserving, 250);
      return;
    }
    observer.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true });
  };

  startObserving();
}

function observeTitlePoller() {
  setInterval(checkTitleChange, 2000);
}

function handleUrlChange() {
  updateActiveRules();
  scanDocument();
}

function instrumentHistory() {
  const wrap = (methodName) => {
    const original = history[methodName];
    if (typeof original !== 'function') {
      return;
    }
    history[methodName] = function wrappedHistoryMethod(...args) {
      const result = original.apply(this, args);
      handleUrlChange();
      return result;
    };
  };
  wrap('pushState');
  wrap('replaceState');
}

function showTemporaryMessage(message, type = 'info') {
  if (!document.body) {
    console.log(`[notify-watcher] ${message}`);
    return;
  }
  if (toastElement) {
    toastElement.remove();
    toastElement = null;
  }
  const toast = document.createElement('div');
  toast.textContent = message;
  toast.style.position = 'fixed';
  toast.style.top = '16px';
  toast.style.right = '16px';
  toast.style.zIndex = '2147483647';
  toast.style.padding = '12px 16px';
  toast.style.background = type === 'error' ? '#B00020' : '#323232';
  toast.style.color = '#fff';
  toast.style.fontSize = '13px';
  toast.style.lineHeight = '18px';
  toast.style.borderRadius = '6px';
  toast.style.boxShadow = '0 4px 12px rgba(0,0,0,0.25)';
  toast.style.pointerEvents = 'none';
  document.body.appendChild(toast);
  toastElement = toast;
  setTimeout(() => {
    if (toastElement === toast) {
      toast.remove();
      toastElement = null;
    }
  }, 4000);
}

function escapeCss(value) {
  if (typeof CSS !== 'undefined' && CSS.escape) {
    return CSS.escape(value);
  }
  return String(value).replace(/([ !"#$%&'()*+,./:;<=>?@[\\\]^`{|}~])/g, '\\$1');
}

function buildCssSelector(element) {
  if (!(element instanceof Element)) {
    return '';
  }
  if (element.id) {
    return `#${escapeCss(element.id)}`;
  }
  const parts = [];
  let current = element;
  while (current && current.nodeType === 1 && current !== document.documentElement) {
    let part = current.nodeName.toLowerCase();
    if (current.classList.length) {
      const classNames = Array.from(current.classList).slice(0, 3).map(escapeCss);
      if (classNames.length) {
        part += '.' + classNames.join('.');
      }
    }
    const parent = current.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter(child => child.nodeName === current.nodeName);
      if (siblings.length > 1) {
        const index = siblings.indexOf(current) + 1;
        part += `:nth-of-type(${index})`;
      }
    }
    parts.unshift(part);
    if (current.id) {
      parts.unshift(`#${escapeCss(current.id)}`);
      break;
    }
    current = parent;
  }
  return parts.join(' > ');
}

async function sendPendingRule(rule) {
  try {
    const response = await fetch(`${API_BASE}/pending_rule`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rule)
    });
    if (!response.ok) {
      throw new Error(`Status ${response.status}`);
    }
    showTemporaryMessage('Elemento capturado! Revise no app desktop.');
  } catch (error) {
    console.error('[notify-watcher] Falha ao enviar regra pendente:', error);
    showTemporaryMessage('Não foi possível enviar a regra capturada.', 'error');
  }
}

function createSelectionOverlay() {
  if (selectionState.overlay) {
    return;
  }
  const overlay = document.createElement('div');
  overlay.style.position = 'fixed';
  overlay.style.left = '0';
  overlay.style.top = '0';
  overlay.style.right = '0';
  overlay.style.bottom = '0';
  overlay.style.zIndex = '2147483646';
  overlay.style.pointerEvents = 'none';

  const highlight = document.createElement('div');
  highlight.style.position = 'absolute';
  highlight.style.pointerEvents = 'none';
  highlight.style.border = '2px solid #00BCD4';
  highlight.style.background = 'rgba(0, 188, 212, 0.15)';
  highlight.style.borderRadius = '4px';

  const infoBox = document.createElement('div');
  infoBox.textContent = 'Clique no elemento para monitorar. ESC para cancelar.';
  infoBox.style.position = 'fixed';
  infoBox.style.left = '50%';
  infoBox.style.top = '16px';
  infoBox.style.transform = 'translateX(-50%)';
  infoBox.style.padding = '10px 16px';
  infoBox.style.background = '#00BCD4';
  infoBox.style.color = '#000';
  infoBox.style.fontSize = '13px';
  infoBox.style.fontWeight = '500';
  infoBox.style.borderRadius = '999px';
  infoBox.style.pointerEvents = 'none';
  infoBox.style.boxShadow = '0 4px 12px rgba(0,0,0,0.2)';

  overlay.appendChild(highlight);
  overlay.appendChild(infoBox);
  document.documentElement.appendChild(overlay);

  selectionState.overlay = overlay;
  selectionState.highlight = highlight;
  selectionState.infoBox = infoBox;
}

function removeSelectionOverlay() {
  if (selectionState.overlay && selectionState.overlay.parentElement) {
    selectionState.overlay.parentElement.removeChild(selectionState.overlay);
  }
  selectionState.overlay = null;
  selectionState.highlight = null;
  selectionState.infoBox = null;
  selectionState.lastTarget = null;
}

function updateHighlight(target) {
  if (!selectionState.highlight || !(target instanceof Element)) {
    return;
  }
  const rect = target.getBoundingClientRect();
  selectionState.highlight.style.left = `${rect.left + window.scrollX}px`;
  selectionState.highlight.style.top = `${rect.top + window.scrollY}px`;
  selectionState.highlight.style.width = `${Math.max(rect.width, 2)}px`;
  selectionState.highlight.style.height = `${Math.max(rect.height, 2)}px`;
  if (selectionState.infoBox) {
    selectionState.infoBox.textContent = `Tag: ${target.tagName.toLowerCase()} | id: ${target.id || '-'} | classes: ${Array.from(target.classList).join(' ') || '-'}`;
  }
}

function handleSelectionMouseMove(event) {
  if (!selectionState.active) {
    return;
  }
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }
  selectionState.lastTarget = target;
  updateHighlight(target);
}

function handleSelectionClick(event) {
  if (!selectionState.active) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  const target = selectionState.lastTarget instanceof Element ? selectionState.lastTarget : (event.target instanceof Element ? event.target : null);
  if (target) {
    captureElement(target);
  } else {
    showTemporaryMessage('Não foi possível identificar o elemento selecionado.', 'error');
  }
  stopSelectionMode();
}

function handleSelectionKeydown(event) {
  if (!selectionState.active) {
    return;
  }
  if (event.key === 'Escape') {
    event.preventDefault();
    stopSelectionMode();
  }
}

function startSelectionMode() {
  if (selectionState.active) {
    return;
  }
  selectionState.active = true;
  createSelectionOverlay();
  document.addEventListener('mousemove', handleSelectionMouseMove, true);
  document.addEventListener('click', handleSelectionClick, true);
  document.addEventListener('keydown', handleSelectionKeydown, true);
  showTemporaryMessage('Selecione um elemento na página…');
}

function stopSelectionMode() {
  if (!selectionState.active) {
    return;
  }
  selectionState.active = false;
  document.removeEventListener('mousemove', handleSelectionMouseMove, true);
  document.removeEventListener('click', handleSelectionClick, true);
  document.removeEventListener('keydown', handleSelectionKeydown, true);
  removeSelectionOverlay();
}

function captureElement(element) {
  const cssSelector = buildCssSelector(element);
  const textSnapshot = getElementText(element);
  const hasText = Boolean(textSnapshot);
  const suggestedCondition = hasText ? 'text_differs' : 'element';
  const payload = {
    name: hasText ? textSnapshot.slice(0, 60) : `${element.tagName.toLowerCase()} (${cssSelector})`,
    page_url: window.location.href,
    url_contains: window.location.hostname,
    type: 'element',
    selector: cssSelector,
    css_selector: cssSelector,
    condition: suggestedCondition,
    text_snapshot: textSnapshot,
    baseline_text: textSnapshot,
    length_threshold: hasText ? textSnapshot.length : undefined,
    source: 'extension',
    captured_at: Date.now() / 1000,
    metadata: {
      tag: element.tagName.toLowerCase(),
      id: element.id || null,
      classes: Array.from(element.classList)
    }
  };
  const attributeEntries = getRelevantAttributeEntries(element);
  if (attributeEntries.length) {
    const attributeMap = {};
    for (const [attrName, attrValue] of attributeEntries) {
      attributeMap[attrName] = attrValue;
    }
    payload.metadata.attributes = attributeMap;
  }
  if (textSnapshot) {
    payload.metadata.text_fingerprint = textSnapshot.slice(0, 120);
  }
  void sendPendingRule(payload);
}

function handleGlobalKeydown(event) {
  if (event.altKey && event.shiftKey && event.key.toLowerCase() === 'n') {
    event.preventDefault();
    if (selectionState.active) {
      stopSelectionMode();
    } else {
      startSelectionMode();
    }
  } else if (event.key === 'Escape' && selectionState.active) {
    stopSelectionMode();
  }
}

async function initialize() {
  console.log('[notify-watcher] Script injetado.');
  await loadConfig(true);
  observeDom();
  observeTitlePoller();
  window.addEventListener('hashchange', handleUrlChange);
  window.addEventListener('popstate', handleUrlChange);
  document.addEventListener('keydown', handleGlobalKeydown, false);
  instrumentHistory();
  setInterval(() => loadConfig(false), CONFIG_REFRESH_INTERVAL_MS);
  setInterval(rescanAllRules, RESCAN_INTERVAL_MS);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initialize);
} else {
  initialize();
}
