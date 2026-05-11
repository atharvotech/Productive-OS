/**
 * Focus Engine Pro — Content Script
 * 
 * Runs on every page. Extracts metadata (title, description, h1)
 * and sends to background.js for classification.
 * If focus mode is ON and page is non-study, shows a full-screen warning overlay.
 */

(function () {
  "use strict";

  // Don't run on extension pages, chrome:// or data: URLs
  if (
    window.location.protocol === "chrome-extension:" ||
    window.location.protocol === "chrome:" ||
    window.location.protocol === "data:"
  ) {
    return;
  }

  // ─── Extract Page Metadata ───────────────────────────────────────────

  function getPageMetadata() {
    const title = document.title || "";
    const metaDesc =
      document.querySelector('meta[name="description"]')?.content || "";
    const metaKeywords =
      document.querySelector('meta[name="keywords"]')?.content || "";
    const h1 = document.querySelector("h1")?.textContent?.trim() || "";
    const ogTitle =
      document.querySelector('meta[property="og:title"]')?.content || "";
    const ogDesc =
      document.querySelector('meta[property="og:description"]')?.content || "";

    return {
      title,
      description: metaDesc,
      keywords: metaKeywords,
      h1,
      ogTitle,
      ogDesc,
      url: window.location.href,
      domain: window.location.hostname.replace("www.", ""),
    };
  }

  // ─── Blocked Overlay ────────────────────────────────────────────────

  function showBlockedOverlay(reason) {
    // Don't add multiple overlays
    if (document.getElementById("fep-blocked-overlay")) return;

    const overlay = document.createElement("div");
    overlay.id = "fep-blocked-overlay";
    overlay.innerHTML = `
      <div style="
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        z-index: 2147483647;
        background: linear-gradient(135deg, #0a0e27 0%, #1a1040 50%, #0a0e27 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        font-family: 'Segoe UI', Inter, -apple-system, sans-serif;
        color: #fff;
      ">
        <div style="
          text-align: center;
          max-width: 480px;
          padding: 2rem;
          background: rgba(255,255,255,0.05);
          backdrop-filter: blur(20px);
          border: 1px solid rgba(255,255,255,0.1);
          border-radius: 24px;
        ">
          <div style="font-size: 3.5rem; margin-bottom: 1rem;">🚫</div>
          <h1 style="font-size: 1.6rem; margin-bottom: 0.5rem; color: #f87171;">
            Focus Mode Active!
          </h1>
          <p style="color: rgba(255,255,255,0.7); margin-bottom: 1rem; line-height: 1.5;">
            This page doesn't look study-related. Focus Engine Pro has blocked it.
          </p>
          <div style="
            background: rgba(248,113,113,0.15);
            border: 1px solid rgba(248,113,113,0.3);
            border-radius: 12px;
            padding: 0.6rem 1rem;
            font-size: 0.8rem;
            color: #fca5a5;
            margin-bottom: 1.5rem;
          ">${reason}</div>
          <p style="color: rgba(255,255,255,0.4); font-size: 0.85rem; font-style: italic;">
            Redirecting in <span id="fep-countdown">10</span> seconds...
          </p>
        </div>
      </div>
    `;

    document.documentElement.appendChild(overlay);

    // Countdown and redirect
    let count = 10;
    const countdownEl = document.getElementById("fep-countdown");
    const timer = setInterval(() => {
      count--;
      if (countdownEl) countdownEl.textContent = count;
      if (count <= 0) {
        clearInterval(timer);
        // Navigate to new tab
        window.location.href = "about:blank";
      }
    }, 1000);
  }

  // ─── Send Metadata to Background ────────────────────────────────────

  // Wait a moment for page to fully load metadata
  setTimeout(() => {
    const metadata = getPageMetadata();

    chrome.runtime.sendMessage(
      { type: "page_metadata", ...metadata },
      (response) => {
        if (chrome.runtime.lastError) return;
        if (response && response.action === "block") {
          showBlockedOverlay(response.reason);
        }
      }
    );
  }, 1500);

  // Also re-check when title changes (SPA navigation)
  let lastTitle = document.title;
  const titleObserver = new MutationObserver(() => {
    if (document.title !== lastTitle) {
      lastTitle = document.title;
      const metadata = getPageMetadata();
      chrome.runtime.sendMessage(
        { type: "page_metadata", ...metadata },
        (response) => {
          if (chrome.runtime.lastError) return;
          if (response && response.action === "block") {
            showBlockedOverlay(response.reason);
          }
        }
      );
    }
  });

  const titleEl = document.querySelector("title");
  if (titleEl) {
    titleObserver.observe(titleEl, { childList: true, characterData: true, subtree: true });
  }

  // ─── Media Playing Detection ─────────────────────────────────────────
  // Detects <video> and <audio> play/pause state and reports to background.js
  // so the backend knows if study content is actually being watched.

  let _lastMediaState = null;

  function reportMediaState(playing) {
    if (playing === _lastMediaState) return; // No change, skip
    _lastMediaState = playing;
    try {
      chrome.runtime.sendMessage(
        { type: "media_status", playing: playing, url: window.location.href },
        () => { if (chrome.runtime.lastError) { /* ignored */ } }
      );
    } catch (e) { /* extension context invalidated */ }
  }

  function checkAnyMediaPlaying() {
    const mediaElements = document.querySelectorAll("video, audio");
    for (const el of mediaElements) {
      if (!el.paused && !el.ended && el.readyState > 2) {
        reportMediaState(true);
        return;
      }
    }
    reportMediaState(false);
  }

  function attachMediaListeners(el) {
    if (el._fepListenersAttached) return;
    el._fepListenersAttached = true;
    el.addEventListener("play", () => checkAnyMediaPlaying());
    el.addEventListener("pause", () => checkAnyMediaPlaying());
    el.addEventListener("ended", () => checkAnyMediaPlaying());
    el.addEventListener("playing", () => checkAnyMediaPlaying());
  }

  // Attach to existing media elements
  document.querySelectorAll("video, audio").forEach(attachMediaListeners);

  // Watch for dynamically added media (YouTube SPA, pw.live player, etc.)
  const mediaObserver = new MutationObserver((mutations) => {
    for (const mut of mutations) {
      for (const node of mut.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.tagName === "VIDEO" || node.tagName === "AUDIO") {
          attachMediaListeners(node);
        }
        // Also check children of added nodes
        if (node.querySelectorAll) {
          node.querySelectorAll("video, audio").forEach(attachMediaListeners);
        }
      }
    }
  });
  mediaObserver.observe(document.documentElement, { childList: true, subtree: true });

  // Periodic heartbeat — re-check every 3 seconds in case events were missed
  setInterval(checkAnyMediaPlaying, 3000);
})();
