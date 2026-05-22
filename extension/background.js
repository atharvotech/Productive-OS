/**
 * Focus Engine Pro — Chrome Extension Background Service Worker
 * 
 * Tracks time spent per domain, syncs to Python WebSocket server every 30s,
 * blocks distraction URLs based on current mode (off/productive/study),
 * and manages mode state from the server.
 * 
 * Modes:
 *   - OFF: Only always-blocked content (adult sites)
 *   - PRODUCTIVE: Soft blocks with time limits + reminders (e.g. Reddit 10min)
 *   - STUDY: Full blockage, strict rules
 * 
 * IMPORTANT: Only tracks time when:
 * 1. The Chrome window is FOCUSED (foreground)
 * 2. For "study" sites in STUDY mode, the window must be MAXIMIZED
 */

// ─── Configuration ───────────────────────────────────────────────────────

const WS_URL = "ws://localhost:8765";
const SYNC_INTERVAL_SEC = 30;
const FOCUS_CHECK_INTERVAL_SEC = 15;

// Always-blocked URL keywords (adult content — blocked in ALL modes, even "off")
const ALWAYS_BLOCKED_KEYWORDS = [
  "pornhub", "xvideos", "xnxx", "xhamster", "redtube",
  "youporn", "spankbang", "brazzers", "onlyfans",
];

// Distraction URL keywords — blocked only in productive/study mode (NOT when "off")
const MODE_BLOCKED_KEYWORDS = [
  "tiktok.com", "/reels", "/shorts",
];

// Study-mode blocked domains (fully blocked in study mode)
const STUDY_BLOCKED_DOMAINS = [
  "instagram.com", "facebook.com", "twitter.com", "x.com",
  "reddit.com", "snapchat.com", "pinterest.com", "tumblr.com",
  "twitch.tv", "netflix.com", "disneyplus.com", "hotstar.com",
  "primevideo.com", "hulu.com", "crunchyroll.com",
  "hbomax.com", "peacocktv.com",
  "9gag.com", "buzzfeed.com", "imgur.com",
];

// Productive-mode: social media distractions blocked (devs don't need these)
const PRODUCTIVE_ALWAYS_BLOCKED = [
  "instagram.com", "snapchat.com", "tiktok.com",
];

// Productive-mode: allowed with time limits (defaults, server can override)
const PRODUCTIVE_TIMED_DEFAULTS = {
  "reddit.com": 10,     // 10 minutes
  "youtube.com": 15,    // 15 minutes (non-study content)
};

// Focus-mode blocked URL keywords
const BLOCKED_TITLE_KEYWORDS = [
  "gaming", "gameplay", "walkthrough", "let's play",
  "fortnite", "valorant", "gta", "minecraft",
  "movie", "trailer", "memes", "funny",
  "unboxing", "haul", "vlog", "mukbang",
  "asmr",
];

// Study-safe domains (never blocked in any mode)
const STUDY_SAFE_DOMAINS = [
  "stackoverflow.com", "github.com", "gitlab.com",
  "docs.python.org", "docs.microsoft.com", "learn.microsoft.com",
  "developer.mozilla.org", "w3schools.com",
  "geeksforgeeks.org", "tutorialspoint.com",
  "leetcode.com", "hackerrank.com", "codechef.com", "codeforces.com",
  "coursera.org", "udemy.com", "edx.org", "khanacademy.org",
  "codecademy.com", "freecodecamp.org",
  "npmjs.com", "pypi.org", "crates.io",
  "arxiv.org", "scholar.google.com", "wikipedia.org",
  "medium.com", "dev.to", "hashnode.dev",

  "colab.research.google.com",
];

// Productivity tools (AI chat, generic work apps)
const PRODUCTIVITY_DOMAINS = [
  "chat.openai.com", "gemini.google.com", "claude.ai", "chatgpt.com", "localhost", "127.0.0.1",
];

// Whitelisted YouTube channel patterns (user can add more via settings)
let whitelistedChannels = [];
// Whitelisted websites (user-defined study domains)
let whitelistedWebsites = [];

// ─── State ───────────────────────────────────────────────────────────────

let timeData = {};           // { domain: { seconds: N, url: "", title: "" } }
let activeTabId = null;
let activeTabDomain = "";
let activeTabUrl = "";
let activeTabTitle = "";
let lastTickTime = Date.now();
let currentMode = "off";     // "off" | "productive" | "study"
let wsConnection = null;
let wsConnected = false;
let reconnectTimer = null;
let reconnectDelay = 1000;
let browserHasFocus = true;
let activeWindowState = "maximized";

// Productive mode domain timers { domain: { startTime, limitMinutes, reminded } }
let domainTimers = {};
// Productive mode time limits from server
let productiveTimers = { ...PRODUCTIVE_TIMED_DEFAULTS };

// Media playing state — tracked per tab from content.js
let mediaPlayingTabId = null; // Tab ID where media is currently playing
let mediaPlaying = false;     // Is media playing in the active tab?

// ─── WebSocket Connection ────────────────────────────────────────────────

function connectWebSocket() {
  if (wsConnection && wsConnection.readyState === WebSocket.OPEN) return;

  try {
    wsConnection = new WebSocket(WS_URL);

    wsConnection.onopen = () => {
      console.log("[FEP] WebSocket connected");
      wsConnected = true;
      reconnectDelay = 1000;
      sendWS({ action: "get_focus_mode" });
      sendWS({ action: "get_settings" });
    };

    wsConnection.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleServerMessage(data);
      } catch (e) {
        console.error("[FEP] Parse error:", e);
      }
    };

    wsConnection.onclose = () => {
      wsConnected = false;
      scheduleReconnect();
    };

    wsConnection.onerror = () => {
      wsConnected = false;
    };
  } catch (e) {
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
    connectWebSocket();
  }, reconnectDelay);
}

function sendWS(data) {
  if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
    const payload = { ...data, client: "extension" };
    wsConnection.send(JSON.stringify(payload));
  }
}

function handleServerMessage(data) {
  if (data.action === "focus_mode") {
    const newMode = data.mode || "off";  // "off" | "productive" | "study"
    if (newMode !== currentMode) {
      currentMode = newMode;
      chrome.storage.local.set({ currentMode });
      console.log(`[FEP] Mode: ${currentMode}`);
      // Reset domain timers when mode changes
      domainTimers = {};
      // Immediately scan all open tabs and block any that violate new mode
      enforceBlockedTabs();
    }
    // Load productive timers from server
    if (data.productive_timers) {
      productiveTimers = { ...PRODUCTIVE_TIMED_DEFAULTS, ...data.productive_timers };
    }
  } else if (data.action === "focus_mode_changed") {
    currentMode = data.mode || "off";
    chrome.storage.local.set({ currentMode });
    domainTimers = {};
    enforceBlockedTabs();
  } else if (data.action === "settings") {
    const channels = data.data?.whitelisted_channels || "";
    whitelistedChannels = channels.split(",").map(c => c.trim().toLowerCase()).filter(Boolean);
    const websites = data.data?.whitelisted_websites || "";
    whitelistedWebsites = websites.split(",").map(w => w.trim().toLowerCase()).filter(Boolean);
    // Load productive timers from settings
    const timersStr = data.data?.productive_mode_timers || "";
    if (timersStr) {
      timersStr.split(",").forEach(entry => {
        const [d, m] = entry.split(":");
        if (d && m) productiveTimers[d.trim()] = parseInt(m.trim());
      });
    }
  }
}

/**
 * Scan ALL open tabs and block any that violate the current mode.
 * Called immediately when mode changes so already-loaded pages get caught.
 */
function enforceBlockedTabs() {
  if (currentMode === "off") return; // Nothing to enforce in off mode

  chrome.tabs.query({}, (tabs) => {
    for (const tab of tabs) {
      if (!tab.url || tab.url.startsWith("chrome") || tab.url.startsWith("edge") || tab.url.startsWith("data:")) {
        continue;
      }
      const { block, reason } = shouldBlockUrl(tab.url, tab.title || "");
      if (block) {
        const blockedHtml = `data:text/html,${encodeURIComponent(getBlockedPageHTML(reason))}`;
        chrome.tabs.update(tab.id, { url: blockedHtml });
        console.log(`[FEP] Blocked already-open tab: ${tab.url}`);
      }
    }
  });
}

// ─── Time Tracking ───────────────────────────────────────────────────────

function getDomain(url) {
  try {
    if (url.startsWith("chrome://newtab") || url.startsWith("edge://newtab")) {
      return "New Tab";
    }
    const u = new URL(url);
    return u.hostname.replace("www.", "");
  } catch {
    return url.startsWith("chrome:") || url.startsWith("edge:") ? "New Tab" : "";
  }
}

function tickTime() {
  const now = Date.now();
  const elapsed = Math.round((now - lastTickTime) / 1000);
  lastTickTime = now;

  if (browserHasFocus && activeTabDomain && elapsed > 0 && elapsed < 300) {
    if (!timeData[activeTabDomain]) {
      timeData[activeTabDomain] = { seconds: 0, url: "", title: "" };
    }
    timeData[activeTabDomain].seconds += elapsed;
    timeData[activeTabDomain].url = activeTabUrl;
    timeData[activeTabDomain].title = activeTabTitle;

    // Track productive mode domain timers
    if (currentMode === "productive") {
      checkProductiveTimer(activeTabDomain, elapsed);
    }
  }
}

function updateActiveTab() {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (tabs && tabs[0]) {
      const tab = tabs[0];
      activeTabId = tab.id;
      activeTabUrl = tab.url || "";
      activeTabTitle = tab.title || "";
      activeTabDomain = getDomain(activeTabUrl);

      if (tab.windowId) {
        chrome.windows.get(tab.windowId, (win) => {
          if (chrome.runtime.lastError) return;
          activeWindowState = win.state;
        });
      }
    }
  });
}

// ─── Productive Mode Timer Logic ─────────────────────────────────────────

function checkProductiveTimer(domain, elapsedSec) {
  // Check if this domain has a time limit in productive mode
  let matchedDomain = null;
  for (const timedDomain of Object.keys(productiveTimers)) {
    if (domain.includes(timedDomain)) {
      matchedDomain = timedDomain;
      break;
    }
  }
  if (!matchedDomain) return; // No timer for this domain

  // Skip study-safe domains
  if (STUDY_SAFE_DOMAINS.some(sd => domain.includes(sd))) return;

  // Initialize timer for this domain
  if (!domainTimers[matchedDomain]) {
    domainTimers[matchedDomain] = {
      startTime: Date.now(),
      accumulatedSec: 0,
      limitMinutes: productiveTimers[matchedDomain],
      reminded: false,
    };
  }

  const timer = domainTimers[matchedDomain];
  timer.accumulatedSec += elapsedSec;
  const minutesSpent = timer.accumulatedSec / 60;

  // Check if time limit exceeded
  if (minutesSpent >= timer.limitMinutes && !timer.reminded) {
    timer.reminded = true;
    // Show a notification reminder
    showProductiveReminder(matchedDomain, Math.round(minutesSpent));
  }
}

function showProductiveReminder(domain, minutes) {
  // Send notification
  try {
    chrome.notifications.create(`productive_${domain}`, {
      type: "basic",
      iconUrl: "icon.png",
      title: "🕐 Focus Engine Pro — Time Check",
      message: `You've been on ${domain} for ${minutes} minutes. Are you still working?`,
      priority: 2,
      requireInteraction: true,
    });
  } catch (e) {
    console.log("[FEP] Notification error:", e);
  }

  // Also inject a gentle overlay reminder into the active tab
  if (activeTabId) {
    try {
      chrome.scripting.executeScript({
        target: { tabId: activeTabId },
        func: (domain, minutes) => {
          // Remove existing reminder if any
          document.getElementById("fep-productive-reminder")?.remove();
          
          const overlay = document.createElement("div");
          overlay.id = "fep-productive-reminder";
          overlay.innerHTML = `
            <div style="position:fixed;top:20px;right:20px;z-index:999999;
                 background:linear-gradient(135deg,rgba(6,182,212,0.95),rgba(124,58,237,0.95));
                 color:#fff;padding:16px 24px;border-radius:16px;
                 font-family:'Segoe UI',Inter,sans-serif;font-size:14px;
                 box-shadow:0 8px 32px rgba(0,0,0,0.3);max-width:380px;
                 animation:slideInRight 0.4s ease;backdrop-filter:blur(12px);">
              <style>@keyframes slideInRight{from{opacity:0;transform:translateX(100px)}to{opacity:1;transform:translateX(0)}}</style>
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
                <span style="font-size:24px">🕐</span>
                <strong style="font-size:15px">Time Check!</strong>
              </div>
              <p style="margin:0 0 12px;opacity:0.9;line-height:1.4">
                You've been on <strong>${domain}</strong> for <strong>${minutes} min</strong>. Are you still working?
              </p>
              <div style="display:flex;gap:8px;justify-content:flex-end">
                <button onclick="this.closest('#fep-productive-reminder').remove()"
                  style="background:rgba(255,255,255,0.2);border:1px solid rgba(255,255,255,0.3);
                         color:#fff;padding:6px 16px;border-radius:8px;cursor:pointer;font-size:13px;">
                  Got it
                </button>
              </div>
            </div>`;
          document.body.appendChild(overlay);
          // Auto-remove after 30 seconds
          setTimeout(() => overlay.remove(), 30000);
        },
        args: [domain, minutes],
      });
    } catch (e) {
      console.log("[FEP] Script injection error:", e);
    }
  }
}

// ─── Sync to Server ──────────────────────────────────────────────────────

function syncTimeData() {
  tickTime();

  for (const [domain, data] of Object.entries(timeData)) {
    if (data.seconds > 0) {
      const isMaximized = activeWindowState === "maximized" || activeWindowState === "fullscreen";
      let category = classifyDomain(domain, data.title, isMaximized);
      let finalDomain = domain;

      // Special segregation for YouTube into distinct logged apps
      if (domain.includes("youtube.com")) {
        if (data.url.toLowerCase().includes("/shorts")) {
          finalDomain = "YouTube Shorts";
          category = "entertainment";
        } else if (category === "study" || category === "productivity") {
          finalDomain = "YouTube (Study)";
        } else {
          finalDomain = "YouTube";
        }
      }

      sendWS({
        action: "log_web_time",
        domain: finalDomain,
        url: data.url,
        title: data.title,
        seconds: data.seconds,
        category: category,
      });
    }
  }
  timeData = {};
}

function classifyDomain(domain, title = "", isMaximized = true) {
  const d = domain.toLowerCase();
  const t = title.toLowerCase();

  // Check user-whitelisted websites first
  for (const wl of whitelistedWebsites) {
    if (d.includes(wl)) {
      return (currentMode === "study" && !isMaximized) ? "productivity" : "study";
    }
  }

  // Productivity
  if (PRODUCTIVITY_DOMAINS.some(pd => d.includes(pd))) {
    return "productivity";
  }

  // Study sites
  if (STUDY_SAFE_DOMAINS.some(sd => d.includes(sd))) {
    return (currentMode === "study" && !isMaximized) ? "productivity" : "study";
  }

  // Social media
  if (STUDY_BLOCKED_DOMAINS.some(bd => d.includes(bd) && 
      ["instagram", "facebook", "twitter", "x.com", "reddit", "snapchat", "pinterest", "tumblr"]
        .some(s => bd.includes(s)))) return "social";

  // Entertainment
  if (STUDY_BLOCKED_DOMAINS.some(bd => d.includes(bd))) return "entertainment";

  // YouTube
  if (d.includes("youtube.com")) {
    if (whitelistedChannels.length > 0) {
      const isWhitelistedChannel = whitelistedChannels.some(ch => t.includes(ch));
      if (isWhitelistedChannel) {
        return (currentMode === "study" && !isMaximized) ? "productivity" : "study";
      }
    }
    if (STUDY_SAFE_KEYWORDS_IN_TITLE(t)) {
      return (currentMode === "study" && !isMaximized) ? "productivity" : "study";
    }
    if (BLOCKED_TITLE_KEYWORDS.some(kw => t.includes(kw))) return "entertainment";
    return "entertainment";
  }

  // Gaming
  if (BLOCKED_TITLE_KEYWORDS.some(kw => t.includes(kw) || d.includes(kw))) return "gaming";

  return "other";
}

function STUDY_SAFE_KEYWORDS_IN_TITLE(title) {
  const keywords = [
    "tutorial", "course", "lecture", "lesson", "programming",
    "coding", "python", "javascript", "html", "css", "react",
    "node", "algorithm", "data structure", "math", "physics",
    "chemistry", "biology", "history", "geography", "english",
    "science", "engineering", "how to code", "learn",
    "documentation", "explained", "education",
  ];
  return keywords.some(kw => title.includes(kw));
}

// ─── URL Blocking ────────────────────────────────────────────────────────

function shouldBlockUrl(url, title = "") {
  if (!url || url.startsWith("chrome-extension://")) {
    return { block: false, reason: "" };
  }
  if (url.startsWith("chrome://") && !url.includes("newtab")) {
    return { block: false, reason: "" };
  }

  const urlLower = url.toLowerCase();
  const domain = getDomain(url);
  const titleLower = (title || "").toLowerCase();

  // Always blocked (adult content — even when mode is off)
  for (const kw of ALWAYS_BLOCKED_KEYWORDS) {
    if (urlLower.includes(kw)) {
      return { block: true, reason: `Blocked keyword: ${kw}` };
    }
  }

  // Distraction keywords (reels, shorts, tiktok) — only in productive/study mode
  if (currentMode === "productive" || currentMode === "study") {
    for (const kw of MODE_BLOCKED_KEYWORDS) {
      if (urlLower.includes(kw)) {
        return { block: true, reason: `Blocked keyword: ${kw} (${currentMode} mode)` };
      }
    }
  }

  // ── STUDY MODE: Full blockage ──────────────────────────────────────
  if (currentMode === "study") {
    // Check if study-safe or productivity apps
    if (STUDY_SAFE_DOMAINS.some(sd => domain.includes(sd)) || PRODUCTIVITY_DOMAINS.some(pd => domain.includes(pd))) {
      return { block: false, reason: "" };
    }
    // Check user-whitelisted websites
    for (const wl of whitelistedWebsites) {
      if (domain.includes(wl)) return { block: false, reason: "" };
    }
    // YouTube special handling
    if (domain.includes("youtube.com")) {
      if (whitelistedChannels.length > 0) {
        const isWhitelisted = whitelistedChannels.some(ch => 
          urlLower.includes(ch) || titleLower.includes(ch)
        );
        if (isWhitelisted) return { block: false, reason: "" };
      }
      if (STUDY_SAFE_KEYWORDS_IN_TITLE(titleLower)) {
        return { block: false, reason: "" };
      }
      return { block: true, reason: "YouTube non-study content blocked in Study Mode" };
    }
    // Block social/entertainment domains
    for (const bd of STUDY_BLOCKED_DOMAINS) {
      if (domain.includes(bd)) {
        return { block: true, reason: `${bd} blocked in Study Mode` };
      }
    }
    // Block by title keywords
    for (const kw of BLOCKED_TITLE_KEYWORDS) {
      if (titleLower.includes(kw) || urlLower.includes(kw)) {
        return { block: true, reason: `Content keyword "${kw}" blocked in Study Mode` };
      }
    }
  }

  // ── PRODUCTIVE MODE: Soft blocks ───────────────────────────────────
  if (currentMode === "productive") {
    // Always block these even in productive mode
    for (const bd of PRODUCTIVE_ALWAYS_BLOCKED) {
      if (domain.includes(bd)) {
        return { block: true, reason: `${bd} blocked in Productive Mode` };
      }
    }
    // Study-safe domains are always allowed
    if (STUDY_SAFE_DOMAINS.some(sd => domain.includes(sd))) {
      return { block: false, reason: "" };
    }
    for (const wl of whitelistedWebsites) {
      if (domain.includes(wl)) return { block: false, reason: "" };
    }
    // Timed domains (reddit, youtube) — allow but track time
    // The timer logic runs in tickTime, not here
    // Don't block, just let the timer handle reminders
  }

  return { block: false, reason: "" };
}

// ─── Navigation Blocking ─────────────────────────────────────────────────

chrome.webNavigation.onBeforeNavigate.addListener((details) => {
  if (details.frameId !== 0) return;

  const { block, reason } = shouldBlockUrl(details.url);
  if (block) {
    const blockedHtml = `data:text/html,${encodeURIComponent(getBlockedPageHTML(reason))}`;
    chrome.tabs.update(details.tabId, { url: blockedHtml });

    const domain = getDomain(details.url);
    sendWS({
      action: "log_web_time",
      domain: domain,
      url: details.url,
      title: reason,
      seconds: 0,
      category: "blocked",
    });
  }
});

// Also check on tab updates (for SPAs that change title without navigation)
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.title || changeInfo.url) {
    const url = tab.url || "";
    const title = tab.title || "";
    const { block, reason } = shouldBlockUrl(url, title);
    if (block && !url.startsWith("data:")) {
      const blockedHtml = `data:text/html,${encodeURIComponent(getBlockedPageHTML(reason))}`;
      chrome.tabs.update(tabId, { url: blockedHtml });
    }
  }
});

function getBlockedPageHTML(reason) {
  const modeLabel = currentMode === "study" ? "Study Mode" : "Productive Mode";
  return `
<!DOCTYPE html>
<html>
<head>
  <title>🚫 Blocked — Focus Engine Pro</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: linear-gradient(135deg, #0a0e27 0%, #1a1040 50%, #0a0e27 100%);
      color: #fff;
      font-family: 'Segoe UI', Inter, sans-serif;
      text-align: center;
      padding: 2rem;
    }
    .container {
      max-width: 500px;
      background: rgba(255,255,255,0.05);
      backdrop-filter: blur(20px);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 24px;
      padding: 3rem 2rem;
    }
    .icon { font-size: 4rem; margin-bottom: 1rem; }
    h1 { font-size: 1.8rem; margin-bottom: 0.5rem; color: #f87171; }
    .mode-badge {
      display: inline-block;
      padding: 4px 12px;
      border-radius: 20px;
      font-size: 0.75rem;
      font-weight: 600;
      margin-bottom: 1rem;
      background: ${currentMode === "study" ? "rgba(16,185,129,0.2)" : "rgba(6,182,212,0.2)"};
      color: ${currentMode === "study" ? "#10b981" : "#06b6d4"};
      border: 1px solid ${currentMode === "study" ? "rgba(16,185,129,0.3)" : "rgba(6,182,212,0.3)"};
    }
    p { color: rgba(255,255,255,0.7); margin-bottom: 1.5rem; line-height: 1.6; }
    .reason {
      background: rgba(248,113,113,0.15);
      border: 1px solid rgba(248,113,113,0.3);
      border-radius: 12px;
      padding: 0.75rem 1rem;
      font-size: 0.85rem;
      color: #fca5a5;
      margin-bottom: 1.5rem;
    }
    .quote {
      font-style: italic;
      color: rgba(255,255,255,0.5);
      font-size: 0.9rem;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="icon">🚫</div>
    <h1>Get Back to Work!</h1>
    <div class="mode-badge">${modeLabel} Active</div>
    <p>This page has been blocked by Focus Engine Pro.</p>
    <div class="reason">${reason}</div>
    <p class="quote">"The secret of getting ahead is getting started." — Mark Twain</p>
  </div>
</body>
</html>`;
}

// ─── Tab & Window Events ─────────────────────────────────────────────────

chrome.tabs.onActivated.addListener((activeInfo) => {
  tickTime();
  updateActiveTab();
});

chrome.windows.onFocusChanged.addListener((windowId) => {
  tickTime();
  if (windowId === chrome.windows.WINDOW_ID_NONE) {
    // WINDOW_ID_NONE fires when browser enters fullscreen OR actually loses focus.
    // Check if any window is in fullscreen — if so, browser is still active.
    chrome.windows.getAll({ populate: false }, (windows) => {
      const fullscreenWin = windows.find(w => w.state === "fullscreen");
      if (fullscreenWin) {
        // Browser is in fullscreen, NOT defocused — keep tracking
        browserHasFocus = true;
        activeWindowState = "fullscreen";
        // Don't clear activeTabDomain — keep counting time
      } else {
        // Genuinely lost focus (user switched to another app)
        browserHasFocus = false;
        activeTabDomain = "";
      }
    });
  } else {
    browserHasFocus = true;
    chrome.windows.get(windowId, (win) => {
      if (chrome.runtime.lastError) return;
      activeWindowState = win.state;
    });
    updateActiveTab();
  }
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (tabId === activeTabId && (changeInfo.url || changeInfo.title)) {
    tickTime();
    activeTabUrl = tab.url || activeTabUrl;
    activeTabTitle = tab.title || activeTabTitle;
    activeTabDomain = getDomain(activeTabUrl);
  }
});

// ─── Content Script Message Handler ──────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "page_metadata") {
    const url = sender.tab?.url || "";
    const title = message.title || "";
    const { block, reason } = shouldBlockUrl(url, title);
    
    if (block) {
      sendResponse({ action: "block", reason: reason });
    } else {
      sendResponse({ action: "allow" });
    }
  } else if (message.type === "media_status") {
    // Content script reports media play/pause state
    const tabId = sender.tab?.id;
    if (message.playing) {
      mediaPlaying = true;
      mediaPlayingTabId = tabId;
    } else if (tabId === mediaPlayingTabId) {
      // Only clear if THIS tab was the one playing
      mediaPlaying = false;
      mediaPlayingTabId = null;
    }
    sendResponse({ action: "ack" });
  }
  return true;
});

// ─── Alarms (periodic tasks) ─────────────────────────────────────────────

chrome.alarms.create("syncTime", { periodInMinutes: SYNC_INTERVAL_SEC / 60 });
chrome.alarms.create("checkFocus", { periodInMinutes: FOCUS_CHECK_INTERVAL_SEC / 60 });
chrome.alarms.create("tickTime", { periodInMinutes: 0.05 }); // Every 3 seconds
chrome.alarms.create("studyMediaHeartbeat", { periodInMinutes: 0.05 }); // Every 3 seconds

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "syncTime") {
    syncTimeData();
  } else if (alarm.name === "checkFocus") {
    sendWS({ action: "get_focus_mode" });
  } else if (alarm.name === "tickTime") {
    tickTime();
    if (browserHasFocus) updateActiveTab();
  } else if (alarm.name === "studyMediaHeartbeat") {
    // Send study media status to backend if conditions are met:
    // 1. Mode is study
    // 2. Media is playing in active tab
    // 3. Window is maximized/fullscreen
    // 4. Active domain is study-classified
    if (currentMode !== "study" || !mediaPlaying || !browserHasFocus) return;
    if (mediaPlayingTabId !== activeTabId) return;

    const isMaximized = activeWindowState === "maximized" || activeWindowState === "fullscreen";
    if (!isMaximized) return;

    const category = classifyDomain(activeTabDomain, activeTabTitle, true);
    if (category !== "study" && category !== "productivity") return;

    sendWS({
      action: "study_media_tick",
      domain: activeTabDomain,
      title: activeTabTitle,
      seconds: 3, // heartbeat interval
    });
  }
});

// ─── Initialize ──────────────────────────────────────────────────────────

chrome.storage.local.get(["currentMode"], (result) => {
  currentMode = result.currentMode || "off";
});

connectWebSocket();
updateActiveTab();

console.log("[FEP] Focus Engine Pro extension loaded (dual mode)");
