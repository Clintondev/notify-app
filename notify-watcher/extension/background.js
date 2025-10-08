'use strict';

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== 'object') {
    return false;
  }

  if (message.type === 'capture_screenshot') {
    chrome.tabs.captureVisibleTab({ format: 'jpeg', quality: 80 }, dataUrl => {
      if (chrome.runtime.lastError || !dataUrl) {
        const error = chrome.runtime.lastError ? chrome.runtime.lastError.message : 'capture failed';
        sendResponse({ error });
        return;
      }
      sendResponse({ image: dataUrl });
    });
    return true; // keep the message channel open for async response
  }

  return false;
});
