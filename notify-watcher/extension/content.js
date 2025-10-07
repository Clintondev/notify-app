'use strict';

const API_BASE = 'http://localhost:3000';
const CONFIG_REFRESH_INTERVAL_MS = 15000;
const RECENT_BUFFER_LIMIT = 50;

let rules = [];
let applicableRules = [];
let ignoredApps = new Set();
let rulesSignature = '';
let lastTitle = document.title || '';

const recentKeys = [];
const recentKeySet = new Set();

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

function notifyServer(payload) {
  fetch(`${API_BASE}/notify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).catch(err => console.error('[notify-watcher] Erro ao enviar para o servidor local:', err));
}

function sendTitleChangeNotification(newTitle) {
  const summary = `Título alterado para: ${newTitle}`;
  const key = `__title__|${summary}`;
  if (ignoredApps.has('Browser')) {
    return;
  }
  if (!rememberNotification(key)) {
    return;
  }
  console.log(`[notify-watcher] Mudança de título detectada: ${newTitle}`);
  notifyServer({ app: 'Browser', text: summary });
}

function extractElementSummary(element) {
  const text = element.innerText || element.textContent || '';
  if (!text) {
    return '';
  }
  const lines = text.split('\n').map(line => line.trim()).filter(Boolean);
  return normalizeText(lines.length ? lines[0] : text);
}

function extractTextMatchSummary(text, needle) {
  if (!text) {
    return '';
  }
  const normalized = text.replace(/\s+/g, ' ').trim();
  if (!needle) {
    return normalizeText(normalized);
  }
  const lowerNeedle = needle.toLowerCase();
  const lowerText = normalized.toLowerCase();
  const index = lowerText.indexOf(lowerNeedle);
  if (index === -1) {
    return normalizeText(normalized);
  }
  const start = Math.max(0, index - 40);
  const end = Math.min(normalized.length, index + needle.length + 40);
  return normalizeText(normalized.slice(start, end));
}

function triggerNotification(rule, matchedText) {
  const appName = rule.name || 'WebApp';
  if (ignoredApps.has(appName)) {
    return;
  }
  const summary = normalizeText(matchedText) || `Regra '${appName}' acionada (${rule.selector})`;
  const key = `${appName}|${rule.type}|${rule.selector}|${summary}`;
  if (!rememberNotification(key)) {
    return;
  }
  console.log(`[notify-watcher] Regra acionada (${appName}): ${summary}`);
  notifyServer({ app: appName, text: summary });
}

function evaluateElement(element) {
  if (!(element instanceof Element)) {
    return;
  }
  for (const rule of applicableRules) {
    if (!rule.selector) {
      continue;
    }
    if (rule.type === 'element') {
      if (element.matches(rule.selector)) {
        triggerNotification(rule, extractElementSummary(element));
      }
    } else if (rule.type === 'element_text') {
      const textContent = element.innerText || element.textContent || '';
      if (textContent && textContent.includes(rule.selector)) {
        triggerNotification(rule, extractTextMatchSummary(textContent, rule.selector));
      }
    }
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

function ruleMatchesCurrentUrl(rule) {
  if (!rule.url_contains) {
    return true;
  }
  return window.location.href.includes(rule.url_contains);
}

function updateApplicableRules() {
  applicableRules = rules.filter(ruleMatchesCurrentUrl);
}

function sanitizeRule(rawRule) {
  if (!rawRule || typeof rawRule !== 'object') {
    return null;
  }
  const name = typeof rawRule.name === 'string' ? rawRule.name : '';
  const cleanName = name.trim();
  const urlContains = typeof rawRule.url_contains === 'string' ? rawRule.url_contains : '';
  const cleanUrlContains = urlContains.trim();
  const selector = typeof rawRule.selector === 'string' ? rawRule.selector : '';
  const cleanSelector = selector.trim();
  const typeValue = typeof rawRule.type === 'string' ? rawRule.type : 'element';
  const type = typeValue === 'element_text' ? 'element_text' : 'element';
  if (!cleanSelector) {
    return null;
  }
  return {
    name: cleanName || 'Regra',
    url_contains: cleanUrlContains,
    selector: cleanSelector,
    type
  };
}

function applyConfig(data, initialScan) {
  const incomingRules = Array.isArray(data?.rules) ? data.rules : Array.isArray(data) ? data : [];
  const sanitized = incomingRules.map(sanitizeRule).filter(Boolean);
  const incomingIgnored = Array.isArray(data?.ignored_apps) ? data.ignored_apps : [];
  const ignored = incomingIgnored.map(item => String(item).trim()).filter(Boolean);

  const nextSignature = JSON.stringify({ rules: sanitized, ignored });
  const changed = nextSignature !== rulesSignature;
  rulesSignature = nextSignature;

  rules = sanitized;
  ignoredApps = new Set(ignored);
  updateApplicableRules();

  if (initialScan || changed) {
    console.log(`[notify-watcher] Regras carregadas: ${rules.length}, ignorados: ${ignoredApps.size}`);
    scanDocument();
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
      }
    }
  });

  const startObserving = () => {
    if (!document.body) {
      setTimeout(startObserving, 250);
      return;
    }
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  };

  startObserving();
}

function observeTitlePoller() {
  setInterval(checkTitleChange, 2000);
}

function handleUrlChange() {
  updateApplicableRules();
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

async function initialize() {
  console.log('[notify-watcher] Script injetado.');
  await loadConfig(true);
  observeDom();
  observeTitlePoller();
  window.addEventListener('hashchange', handleUrlChange);
  window.addEventListener('popstate', handleUrlChange);
  instrumentHistory();
  setInterval(() => loadConfig(false), CONFIG_REFRESH_INTERVAL_MS);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initialize);
} else {
  initialize();
}
