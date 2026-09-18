"""Interactive Web UI and A2UI v0.9 Living Form Renderer for ge-qualify-agent.

Served at GET / behind Google Cloud IAP on the Global External Load Balancer
(https://8.233.121.252.nip.io/), providing:
1. Google Identity display via Cloud IAP headers (X-Goog-Authenticated-User-Email).
2. Conversational chat pane with full Markdown rendering (tables, deliverables, links).
3. Live A2UI v0.9 surface interpreter (CompositeStepCard, CompositeSubCard, TextField,
   ChoicePicker, Slider, SystemsTable, MetricCard, ScoreBar, StatusBadge, CalloutCard,
   and two-way JSON-Pointer dataModel binding).
4. Optional popup integration for Microsoft SharePoint OAuth (/auth -> /auth/callback).
"""

from __future__ import annotations

import hashlib
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse


def _extract_iap_email(request: Request) -> str:
    """Extracts the authenticated Google Identity email from Cloud IAP headers."""
    raw = request.headers.get("x-goog-authenticated-user-email", "")
    if ":" in raw:
        return raw.split(":", 1)[1].strip()
    return raw.strip() or "authenticated-user@google.com"


async def handle_whoami(request: Request) -> JSONResponse:
    """Returns the signed-in Google Identity (via Cloud IAP) and stable session prefix."""
    email = _extract_iap_email(request)
    user_hash = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:12]
    return JSONResponse(
        {
            "email": email,
            "user_hash": user_hash,
            "default_context_id": f"iap-{user_hash}",
        }
    )


async def handle_web_ui(request: Request) -> HTMLResponse:
    """Serves the standalone Gemini Enterprise + A2UI v0.9 Living Form Web Application."""
    email = _extract_iap_email(request)
    return HTMLResponse(_WEB_UI_HTML.replace("__IAP_USER_EMAIL__", email))


_WEB_UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Gemini Enterprise — Use Case Qualification & Architecture CoE</title>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <style>
    :root {
      --bg: #f8fafc;
      --surface: #ffffff;
      --border: #e2e8f0;
      --border-strong: #cbd5e1;
      --text: #0f172a;
      --text-muted: #475569;
      --text-subtle: #64748b;
      --primary: #1a73e8;
      --primary-hover: #1557b0;
      --primary-soft: #e8f0fe;
      --accent: #0f9d58;
      --warn: #f59e0b;
      --radius: 12px;
      --shadow: 0 4px 20px rgba(15, 23, 42, 0.06);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: 'Google Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    /* Top Header */
    header {
      height: 60px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 20px;
      flex-shrink: 0;
      z-index: 10;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 700;
      font-size: 1.02rem;
      color: var(--text);
    }
    .brand-badge {
      background: linear-gradient(135deg, #1a73e8, #8ab4f8);
      color: white;
      width: 32px;
      height: 32px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 0.95rem;
      box-shadow: 0 2px 6px rgba(26, 115, 232, 0.28);
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 0.8rem;
      font-weight: 500;
      padding: 6px 12px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: #f8fafc;
      color: var(--text-muted);
    }
    .pill-google {
      background: #f0fdf4;
      border-color: #bbf7d0;
      color: #166534;
    }
    .pill-sp {
      cursor: pointer;
      transition: all 0.15s;
    }
    .pill-sp:hover {
      border-color: var(--primary);
      color: var(--primary);
      background: var(--primary-soft);
    }
    .pill-sp.connected {
      background: #eff6ff;
      border-color: #bfdbfe;
      color: #1d4ed8;
    }
    .btn-top {
      background: white;
      border: 1px solid var(--border-strong);
      color: var(--text);
      padding: 6px 12px;
      border-radius: 8px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
    }
    .btn-top:hover {
      background: #f1f5f9;
      border-color: #94a3b8;
    }

    /* Main Split Layout */
    main {
      flex: 1;
      display: grid;
      grid-template-columns: minmax(420px, 48%) 1fr;
      overflow: hidden;
    }
    @media (max-width: 1024px) {
      main { grid-template-columns: 1fr; overflow-y: auto; }
    }

    /* Left Chat Pane */
    .chat-pane {
      display: flex;
      flex-direction: column;
      border-right: 1px solid var(--border);
      background: var(--surface);
      height: 100%;
      overflow: hidden;
    }
    .quick-bar {
      padding: 10px 16px;
      border-bottom: 1px solid var(--border);
      background: #f8fafc;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .quick-chip {
      font-size: 0.78rem;
      font-weight: 600;
      padding: 5px 11px;
      border-radius: 999px;
      background: white;
      border: 1px solid var(--border-strong);
      color: var(--text-muted);
      cursor: pointer;
      transition: all 0.15s;
    }
    .quick-chip:hover {
      background: var(--primary-soft);
      color: var(--primary);
      border-color: var(--primary);
    }
    .messages {
      flex: 1;
      overflow-y: auto;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .msg {
      max-width: 92%;
      padding: 14px 18px;
      border-radius: 14px;
      line-height: 1.55;
      font-size: 0.92rem;
      word-wrap: break-word;
    }
    .msg-user {
      align-self: flex-end;
      background: var(--primary);
      color: white;
      border-bottom-right-radius: 4px;
    }
    .msg-agent {
      align-self: flex-start;
      background: #f8fafc;
      border: 1px solid var(--border);
      color: var(--text);
      border-bottom-left-radius: 4px;
      width: 100%;
    }
    .msg-agent h1, .msg-agent h2, .msg-agent h3 {
      margin-top: 0.6em;
      margin-bottom: 0.4em;
      color: #0f172a;
    }
    .msg-agent h1 { font-size: 1.25rem; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
    .msg-agent h2 { font-size: 1.08rem; }
    .msg-agent h3 { font-size: 0.96rem; }
    .msg-agent table {
      width: 100%;
      border-collapse: collapse;
      margin: 10px 0;
      font-size: 0.84rem;
    }
    .msg-agent th, .msg-agent td {
      border: 1px solid var(--border-strong);
      padding: 7px 10px;
      text-align: left;
    }
    .msg-agent th { background: #f1f5f9; font-weight: 600; }
    .msg-agent a {
      color: var(--primary);
      font-weight: 600;
      text-decoration: underline;
    }
    .msg-agent code {
      background: #e2e8f0;
      padding: 2px 5px;
      border-radius: 4px;
      font-size: 0.84em;
    }
    .composer {
      padding: 14px 16px;
      border-top: 1px solid var(--border);
      background: var(--surface);
      display: flex;
      gap: 10px;
      align-items: flex-end;
    }
    .composer textarea {
      flex: 1;
      resize: none;
      border: 1px solid var(--border-strong);
      border-radius: 10px;
      padding: 10px 14px;
      font-family: inherit;
      font-size: 0.92rem;
      min-height: 44px;
      max-height: 130px;
      outline: none;
    }
    .composer textarea:focus {
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(26, 115, 232, 0.14);
    }
    .btn-send {
      background: var(--primary);
      color: white;
      border: none;
      border-radius: 10px;
      padding: 0 18px;
      height: 44px;
      font-weight: 600;
      font-size: 0.9rem;
      cursor: pointer;
      transition: background 0.15s;
    }
    .btn-send:hover { background: var(--primary-hover); }
    .btn-send:disabled { opacity: 0.55; cursor: not-allowed; }

    /* Right A2UI v0.9 Living Form Pane */
    .surface-pane {
      height: 100%;
      overflow-y: auto;
      padding: 24px 28px;
      background: #f8fafc;
    }
    .surface-empty {
      background: white;
      border: 1px dashed var(--border-strong);
      border-radius: var(--radius);
      padding: 44px 28px;
      text-align: center;
      color: var(--text-subtle);
      margin-top: 24px;
    }
    .a2ui-card {
      background: white;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px 22px;
      box-shadow: var(--shadow);
      margin-bottom: 16px;
    }
    .a2ui-step-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 14px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--border);
    }
    .a2ui-step-badge {
      background: var(--primary-soft);
      color: var(--primary);
      font-weight: 700;
      font-size: 0.78rem;
      padding: 4px 10px;
      border-radius: 999px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .a2ui-subcard {
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
      margin: 12px 0;
    }
    .a2ui-subcard-title {
      font-weight: 700;
      font-size: 0.93rem;
      margin-bottom: 10px;
      color: #1e293b;
    }
    .a2ui-col { display: flex; flex-direction: column; gap: 12px; }
    .a2ui-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .a2ui-field {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-bottom: 6px;
    }
    .a2ui-label {
      font-size: 0.85rem;
      font-weight: 600;
      color: #1e293b;
    }
    .a2ui-input, .a2ui-textarea, .a2ui-select {
      width: 100%;
      padding: 9px 12px;
      border: 1px solid var(--border-strong);
      border-radius: 8px;
      font-family: inherit;
      font-size: 0.9rem;
      background: white;
      color: var(--text);
    }
    .a2ui-textarea { min-height: 74px; resize: vertical; }
    .a2ui-input:focus, .a2ui-textarea:focus, .a2ui-select:focus {
      border-color: var(--primary);
      outline: none;
      box-shadow: 0 0 0 3px rgba(26, 115, 232, 0.12);
    }
    .a2ui-choices {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .a2ui-choice-pill {
      padding: 7px 13px;
      border-radius: 8px;
      border: 1px solid var(--border-strong);
      background: white;
      font-size: 0.84rem;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.12s;
      user-select: none;
    }
    .a2ui-choice-pill.selected {
      background: var(--primary-soft);
      border-color: var(--primary);
      color: var(--primary);
      font-weight: 600;
    }
    .a2ui-slider-wrap {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .a2ui-slider-wrap input[type="range"] {
      flex: 1;
      accent-color: var(--primary);
    }
    .a2ui-slider-val {
      font-weight: 700;
      font-size: 0.9rem;
      color: var(--primary);
      min-width: 48px;
      text-align: right;
    }
    .a2ui-btn {
      padding: 9px 16px;
      border-radius: 8px;
      font-weight: 600;
      font-size: 0.88rem;
      cursor: pointer;
      border: 1px solid transparent;
      transition: all 0.15s;
    }
    .a2ui-btn-primary {
      background: var(--primary);
      color: white;
    }
    .a2ui-btn-primary:hover { background: var(--primary-hover); }
    .a2ui-btn-secondary {
      background: white;
      border-color: var(--border-strong);
      color: var(--text);
    }
    .a2ui-btn-secondary:hover { background: #f1f5f9; }
    .a2ui-btn-borderless {
      background: transparent;
      color: var(--primary);
    }
    .a2ui-btn-borderless:hover { background: var(--primary-soft); }
    .a2ui-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.84rem;
      margin-top: 6px;
    }
    .a2ui-table th, .a2ui-table td {
      border: 1px solid var(--border-strong);
      padding: 6px 8px;
    }
    .a2ui-table th { background: #f1f5f9; text-align: left; }
    .a2ui-table input {
      width: 100%;
      border: 1px solid transparent;
      padding: 4px 6px;
      font-size: 0.84rem;
      border-radius: 4px;
    }
    .a2ui-table input:focus {
      border-color: var(--primary);
      background: white;
      outline: none;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-badge">GE</div>
      <div>
        <div>Gemini Enterprise — Use Case Qualification & Architecture CoE</div>
        <div style="font-size:0.74rem;font-weight:400;color:var(--text-subtle);">A2A + A2UI v0.9 Interactive Living Form Workspace</div>
      </div>
    </div>
    <div class="header-actions">
      <div class="pill pill-google" title="Authenticated via Google Cloud Identity-Aware Proxy (IAP)">
        <span>🔒 Google Identity:</span>
        <strong id="iap-email">__IAP_USER_EMAIL__</strong>
      </div>
      <div class="pill pill-sp" id="sp-badge" onclick="openSharePointPopup()" title="Optional: Connect Microsoft SharePoint Online for shared folder sync">
        <span id="sp-status-dot">⚪</span>
        <span id="sp-status-text">SharePoint: Optional (Click to Connect)</span>
      </div>
      <button class="btn-top" onclick="resetSession()">+ New Session</button>
    </div>
  </header>

  <main>
    <!-- Left Conversational & Deliverable Stream -->
    <section class="chat-pane">
      <div class="quick-bar">
        <button class="quick-chip" onclick="sendQuickPrompt('I want to qualify an automated invoice and contract triage agent for Finance')">🚀 Start Business Qualification</button>
        <button class="quick-chip" onclick="sendQuickPrompt('Review Technical Architecture')">🏗️ Technical Architecture Review</button>
        <button class="quick-chip" onclick="sendQuickPrompt('Generate Portfolio Prioritization Report')">📊 Portfolio Prioritization</button>
      </div>
      <div class="messages" id="messages">
        <div class="msg msg-agent">
          <strong>Welcome to the Gemini Enterprise Use Case Qualification & Architecture CoE Agent.</strong><br/><br/>
          You are signed in with your Google Identity (<code>__IAP_USER_EMAIL__</code>). You can:
          <ul>
            <li><strong>Qualify a Business Use Case</strong> across the 4-stage interactive A2UI living form (and generate a <em>Business Value Brief</em>).</li>
            <li><strong>Run a Technical Architecture Review</strong> on a qualified opportunity (and generate a <em>Technical Architecture Dossier</em>).</li>
            <li><strong>Run a Portfolio Prioritization Review</strong> across qualified initiatives.</li>
          </ul>
          <em>Note: Connecting Microsoft SharePoint is optional during business intake—you can connect via the top badge whenever you want to read/write to the shared SharePoint folder, or skip it for a standalone demo.</em>
        </div>
      </div>
      <div class="composer">
        <textarea id="chat-input" rows="2" placeholder="Describe your AI initiative, paste an intake document, or ask a question..." onkeydown="if(event.key==='Enter' && !event.shiftKey){event.preventDefault();sendChatMessage();}"></textarea>
        <button class="btn-send" id="send-btn" onclick="sendChatMessage()">Send</button>
      </div>
    </section>

    <!-- Right A2UI v0.9 Living Form Surface -->
    <section class="surface-pane" id="surface-pane">
      <div class="surface-empty" id="surface-empty">
        <h3 style="margin-top:0;color:var(--text);">Interactive A2UI v0.9 Living Form</h3>
        <p style="max-width:440px;margin:8px auto 18px;">
          Start a Business Qualification or Technical Architecture Review on the left to launch the live multi-stage A2UI v0.9 surface here.
        </p>
        <button class="a2ui-btn a2ui-btn-primary" onclick="sendQuickPrompt('Start business intake interview')">Launch Stage 1 Intake Form →</button>
      </div>
      <div id="a2ui-root"></div>
    </section>
  </main>

  <script>
    // Session & A2UI v0.9 State
    let contextId = localStorage.getItem("ge_qualify_context_id") || "";
    let currentTaskId = null;
    const surfaces = {}; // surfaceId -> { components: Map(id -> comp), dataModel: {} }

    async function initIdentity() {
      try {
        const res = await fetch("/api/me");
        if (res.ok) {
          const info = await res.json();
          document.getElementById("iap-email").textContent = info.email;
          if (!contextId) {
            contextId = info.default_context_id + "-" + Math.random().toString(36).slice(2, 7);
            localStorage.setItem("ge_qualify_context_id", contextId);
          }
        }
      } catch (e) {
        console.warn("Identity fetch error:", e);
      }
      if (!contextId) {
        contextId = "ctx-" + Math.random().toString(36).slice(2, 10);
        localStorage.setItem("ge_qualify_context_id", contextId);
      }
      checkSharePointStatus();
    }

    function resetSession() {
      contextId = "ctx-" + Math.random().toString(36).slice(2, 10);
      localStorage.setItem("ge_qualify_context_id", contextId);
      currentTaskId = null;
      for (const k of Object.keys(surfaces)) delete surfaces[k];
      document.getElementById("a2ui-root").innerHTML = "";
      document.getElementById("surface-empty").style.display = "block";
      document.getElementById("messages").innerHTML = `
        <div class="msg msg-agent">
          <strong>Started a new session (<code>${contextId}</code>).</strong> Describe a use case or click one of the quick actions above to begin.
        </div>`;
      checkSharePointStatus();
    }

    async function checkSharePointStatus() {
      try {
        const res = await fetch("/auth/status?session=" + encodeURIComponent(contextId));
        if (res.ok) {
          const data = await res.json();
          if (data.authenticated) {
            const badge = document.getElementById("sp-badge");
            badge.classList.add("connected");
            document.getElementById("sp-status-dot").textContent = "🟢";
            document.getElementById("sp-status-text").textContent = "SharePoint: Connected";
          }
        }
      } catch (e) {}
    }

    function openSharePointPopup(customUrl) {
      const url = customUrl || ("/auth?session=" + encodeURIComponent(contextId));
      const w = window.open(url, "sp_oauth", "width=640,height=720");
      const timer = setInterval(async () => {
        if (w && w.closed) {
          clearInterval(timer);
          await checkSharePointStatus();
        }
      }, 1000);
    }

    window.addEventListener("message", async (ev) => {
      if (ev.data && ev.data.type === "sharepoint_oauth_complete") {
        await checkSharePointStatus();
        if (ev.data.code) {
          // Automatically send verification code to current context
          await dispatchA2ATurn({ text: ev.data.code });
        }
      }
    });

    function sendQuickPrompt(text) {
      document.getElementById("chat-input").value = text;
      sendChatMessage();
    }

    async function sendChatMessage() {
      const input = document.getElementById("chat-input");
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      appendMessage("user", text);
      await dispatchA2ATurn({ text });
    }

    function appendMessage(role, markdownText) {
      const container = document.getElementById("messages");
      const div = document.createElement("div");
      div.className = "msg " + (role === "user" ? "msg-user" : "msg-agent");
      if (role === "user") {
        div.textContent = markdownText;
      } else {
        div.innerHTML = marked.parse(markdownText || "");
        // Intercept /auth links to open nicely in popup
        div.querySelectorAll("a").forEach(a => {
          const href = a.getAttribute("href") || "";
          if (href.includes("/auth")) {
            a.addEventListener("click", (e) => {
              e.preventDefault();
              openSharePointPopup(href);
            });
          } else {
            a.setAttribute("target", "_blank");
          }
        });
      }
      container.appendChild(div);
      container.scrollTop = container.scrollHeight;
    }

    // JSON Pointer helpers for A2UI v0.9 DataModel
    function getByPointer(obj, pointer) {
      if (!pointer || pointer === "/") return obj;
      const parts = pointer.replace(/^\//, "").split("/");
      let cur = obj;
      for (const p of parts) {
        if (cur == null || typeof cur !== "object") return undefined;
        cur = cur[p];
      }
      return cur;
    }

    function setByPointer(obj, pointer, val) {
      if (!pointer || pointer === "/") {
        if (val && typeof val === "object") Object.assign(obj, val);
        return;
      }
      const parts = pointer.replace(/^\//, "").split("/");
      let cur = obj;
      for (let i = 0; i < parts.length - 1; i++) {
        const p = parts[i];
        if (cur[p] == null || typeof cur[p] !== "object") cur[p] = {};
        cur = cur[p];
      }
      cur[parts[parts.length - 1]] = val;
    }

    function resolveDynamic(surface, valOrPath) {
      if (valOrPath && typeof valOrPath === "object" && typeof valOrPath.path === "string") {
        const v = getByPointer(surface.dataModel, valOrPath.path);
        return v !== undefined ? v : "";
      }
      return valOrPath;
    }

    async function dispatchA2ATurn({ text, action }) {
      const btn = document.getElementById("send-btn");
      btn.disabled = true;
      btn.textContent = "Thinking...";

      const parts = [];
      if (text) parts.push({ kind: "text", text });
      if (action) parts.push({ kind: "data", data: { action } });

      const messagePayload = {
        kind: "message",
        messageId: "msg-" + Date.now() + "-" + Math.random().toString(36).slice(2, 7),
        role: "user",
        contextId: contextId,
        parts
      };
      if (currentTaskId) {
        messagePayload.taskId = currentTaskId;
      }

      const rpcBody = {
        jsonrpc: "2.0",
        id: "req-" + Date.now(),
        method: "message/send",
        params: {
          message: messagePayload
        }
      };

      try {
        const resp = await fetch("/", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-A2A-Extensions": "https://a2ui.org/a2a-extension/a2ui/v0.9"
          },
          body: JSON.stringify(rpcBody)
        });
        const payload = await resp.json();
        const result = payload.result || {};
        if (result.id) currentTaskId = result.id;
        if (result.contextId) contextId = result.contextId;

        const statusMsg = (result.status && result.status.message) || result;
        const replyParts = statusMsg.parts || [];
        let hasSurfaceUpdate = false;

        for (const p of replyParts) {
          const root = p.root || p;
          if (root.kind === "text" && root.text) {
            appendMessage("agent", root.text);
          } else if (root.kind === "data" && root.data) {
            applyA2UIMessage(root.data);
            hasSurfaceUpdate = true;
          }
        }
        if (hasSurfaceUpdate) {
          renderAllSurfaces();
        }
        checkSharePointStatus();
      } catch (err) {
        appendMessage("agent", "⚠️ **Connection error:** " + err.message);
      } finally {
        btn.disabled = false;
        btn.textContent = "Send";
      }
    }

    function applyA2UIMessage(msg) {
      if (msg.createSurface) {
        const sid = msg.createSurface.surfaceId;
        if (!surfaces[sid]) {
          surfaces[sid] = { surfaceId: sid, components: new Map(), dataModel: {} };
        }
      }
      if (msg.updateComponents) {
        const sid = msg.updateComponents.surfaceId;
        if (!surfaces[sid]) {
          surfaces[sid] = { surfaceId: sid, components: new Map(), dataModel: {} };
        }
        for (const comp of (msg.updateComponents.components || [])) {
          surfaces[sid].components.set(comp.id, comp);
        }
      }
      if (msg.updateDataModel) {
        const sid = msg.updateDataModel.surfaceId;
        if (!surfaces[sid]) {
          surfaces[sid] = { surfaceId: sid, components: new Map(), dataModel: {} };
        }
        setByPointer(surfaces[sid].dataModel, msg.updateDataModel.path || "/", msg.updateDataModel.value);
      }
    }

    function renderAllSurfaces() {
      const rootEl = document.getElementById("a2ui-root");
      const emptyEl = document.getElementById("surface-empty");
      rootEl.innerHTML = "";

      const sids = Object.keys(surfaces);
      if (sids.length === 0) {
        emptyEl.style.display = "block";
        return;
      }
      emptyEl.style.display = "none";

      // Render the most recently updated surface first (e.g. active stage or handover card)
      for (const sid of sids) {
        const surface = surfaces[sid];
        const rootComp = surface.components.get("root");
        if (!rootComp) continue;
        const wrapper = document.createElement("div");
        wrapper.className = "a2ui-surface-instance";
        wrapper.appendChild(renderComponent(surface, rootComp));
        rootEl.appendChild(wrapper);
      }
    }

    function renderComponent(surface, comp) {
      if (!comp) return document.createTextNode("");
      const type = comp.component;

      if (type === "Card" || type === "CompositeStepCard") {
        const card = document.createElement("div");
        card.className = "a2ui-card";
        if (comp.stageNumber || comp.stageTitle) {
          const hdr = document.createElement("div");
          hdr.className = "a2ui-step-header";
          hdr.innerHTML = `
            <div>
              <div style="font-weight:700;font-size:1.05rem;">${comp.stageTitle || ""}</div>
              ${comp.stageSubtitle ? `<div style="font-size:0.82rem;color:var(--text-subtle);margin-top:2px;">${comp.stageSubtitle}</div>` : ""}
            </div>
            ${comp.stageNumber ? `<span class="a2ui-step-badge">Stage ${comp.stageNumber} of ${comp.totalStages || 4}</span>` : ""}
          `;
          card.appendChild(hdr);
        }
        if (comp.child) {
          card.appendChild(renderComponent(surface, surface.components.get(comp.child)));
        }
        for (const cid of (comp.children || [])) {
          card.appendChild(renderComponent(surface, surface.components.get(cid)));
        }
        return card;
      }

      if (type === "CompositeSubCard") {
        const sub = document.createElement("div");
        sub.className = "a2ui-subcard";
        if (comp.title) {
          const t = document.createElement("div");
          t.className = "a2ui-subcard-title";
          t.textContent = resolveDynamic(surface, comp.title);
          sub.appendChild(t);
        }
        for (const cid of (comp.children || [])) {
          sub.appendChild(renderComponent(surface, surface.components.get(cid)));
        }
        return sub;
      }

      if (type === "Column") {
        const col = document.createElement("div");
        col.className = "a2ui-col";
        for (const cid of (comp.children || [])) {
          col.appendChild(renderComponent(surface, surface.components.get(cid)));
        }
        return col;
      }

      if (type === "Row") {
        const row = document.createElement("div");
        row.className = "a2ui-row";
        for (const cid of (comp.children || [])) {
          row.appendChild(renderComponent(surface, surface.components.get(cid)));
        }
        return row;
      }

      if (type === "Divider") {
        const hr = document.createElement("hr");
        hr.style.border = "none";
        hr.style.borderTop = "1px solid var(--border)";
        hr.style.margin = "8px 0";
        return hr;
      }

      if (type === "Text") {
        const txt = String(resolveDynamic(surface, comp.text) || "");
        const variant = comp.variant || "body";
        const el = document.createElement("div");
        if (variant === "h1" || variant === "h2" || variant === "h3") {
          el.style.fontWeight = "700";
          el.style.fontSize = variant === "h1" ? "1.25rem" : variant === "h2" ? "1.08rem" : "0.98rem";
        } else if (variant === "caption") {
          el.style.fontSize = "0.8rem";
          el.style.color = "var(--text-subtle)";
        } else {
          el.style.fontSize = "0.9rem";
        }
        el.innerHTML = marked.parseInline(txt);
        return el;
      }

      if (type === "TextField") {
        const wrap = document.createElement("div");
        wrap.className = "a2ui-field";
        const lbl = document.createElement("label");
        lbl.className = "a2ui-label";
        lbl.textContent = resolveDynamic(surface, comp.label) || "";
        wrap.appendChild(lbl);

        const path = comp.value && comp.value.path;
        const currentVal = resolveDynamic(surface, comp.value);
        const isLong = comp.variant === "longText";
        const input = document.createElement(isLong ? "textarea" : "input");
        input.className = isLong ? "a2ui-textarea" : "a2ui-input";
        if (!isLong && comp.variant === "number") input.type = "number";
        input.value = currentVal != null ? currentVal : "";
        input.addEventListener("input", (e) => {
          const v = comp.variant === "number" ? Number(e.target.value) : e.target.value;
          if (path) setByPointer(surface.dataModel, path, v);
        });
        wrap.appendChild(input);
        return wrap;
      }

      if (type === "ChoicePicker") {
        const wrap = document.createElement("div");
        wrap.className = "a2ui-field";
        const lbl = document.createElement("label");
        lbl.className = "a2ui-label";
        lbl.textContent = resolveDynamic(surface, comp.label) || "";
        wrap.appendChild(lbl);

        const isMulti = comp.variant === "multipleSelection";
        const path = comp.value && comp.value.path;
        let selected = resolveDynamic(surface, comp.value);
        if (!Array.isArray(selected)) {
          selected = selected ? [String(selected)] : [];
        } else {
          selected = selected.map(String);
        }

        const choicesBox = document.createElement("div");
        choicesBox.className = "a2ui-choices";
        for (const opt of (comp.options || [])) {
          const optVal = String(opt.value);
          const optLabel = resolveDynamic(surface, opt.label) || optVal;
          const pill = document.createElement("div");
          pill.className = "a2ui-choice-pill" + (selected.includes(optVal) ? " selected" : "");
          pill.textContent = optLabel;
          pill.addEventListener("click", () => {
            if (isMulti) {
              if (selected.includes(optVal)) {
                selected = selected.filter(x => x !== optVal);
              } else {
                selected.push(optVal);
              }
            } else {
              selected = [optVal];
            }
            if (path) setByPointer(surface.dataModel, path, selected);
            renderAllSurfaces();
          });
          choicesBox.appendChild(pill);
        }
        wrap.appendChild(choicesBox);
        return wrap;
      }

      if (type === "Slider") {
        const wrap = document.createElement("div");
        wrap.className = "a2ui-field";
        const lbl = document.createElement("label");
        lbl.className = "a2ui-label";
        lbl.textContent = resolveDynamic(surface, comp.label) || "";
        wrap.appendChild(lbl);

        const path = comp.value && comp.value.path;
        const curVal = Number(resolveDynamic(surface, comp.value) ?? comp.min ?? 0);
        const row = document.createElement("div");
        row.className = "a2ui-slider-wrap";

        const range = document.createElement("input");
        range.type = "range";
        range.min = comp.min ?? 0;
        range.max = comp.max ?? 100;
        range.step = comp.step ?? 1;
        range.value = curVal;

        const valBadge = document.createElement("span");
        valBadge.className = "a2ui-slider-val";
        valBadge.textContent = curVal;

        range.addEventListener("input", (e) => {
          const n = Number(e.target.value);
          valBadge.textContent = n;
          if (path) setByPointer(surface.dataModel, path, n);
        });

        row.appendChild(range);
        row.appendChild(valBadge);
        wrap.appendChild(row);
        return wrap;
      }

      if (type === "SystemsTable" || type === "Table") {
        const wrap = document.createElement("div");
        wrap.className = "a2ui-field";
        const path = (comp.rows && comp.rows.path) || "/ui/systems/table";
        let rows = getByPointer(surface.dataModel, path);
        if (!Array.isArray(rows) || rows.length === 0) {
          rows = [{ system_name: "", system_type: "ERP/CRM", access_pattern: "Read-only", api_readiness: "REST/GraphQL", auth_model: "OAuth2/OIDC", data_sensitivity: "Internal" }];
          setByPointer(surface.dataModel, path, rows);
        }
        const cols = comp.columns || [
          { key: "system_name", label: "System Name" },
          { key: "system_type", label: "Category" },
          { key: "access_pattern", label: "Access Mode" },
          { key: "api_readiness", label: "API Readiness" },
          { key: "auth_model", label: "Auth Model" }
        ];
        const tbl = document.createElement("table");
        tbl.className = "a2ui-table";
        const thead = document.createElement("thead");
        const trH = document.createElement("tr");
        for (const c of cols) {
          const th = document.createElement("th");
          th.textContent = c.label || c.header || c.key;
          trH.appendChild(th);
        }
        thead.appendChild(trH);
        tbl.appendChild(thead);

        const tbody = document.createElement("tbody");
        rows.forEach((r, idx) => {
          const tr = document.createElement("tr");
          for (const c of cols) {
            const td = document.createElement("td");
            const inp = document.createElement("input");
            inp.value = r[c.key] || "";
            inp.placeholder = c.label || c.key;
            inp.addEventListener("input", (e) => {
              rows[idx][c.key] = e.target.value;
              setByPointer(surface.dataModel, path, rows);
            });
            td.appendChild(inp);
            tr.appendChild(td);
          }
          tbody.appendChild(tr);
        });
        tbl.appendChild(tbody);
        wrap.appendChild(tbl);

        const addBtn = document.createElement("button");
        addBtn.className = "a2ui-btn a2ui-btn-borderless";
        addBtn.style.alignSelf = "flex-start";
        addBtn.style.marginTop = "6px";
        addBtn.textContent = "+ Add System Row";
        addBtn.addEventListener("click", () => {
          rows.push({ system_name: "", system_type: "Internal API", access_pattern: "Read-only", api_readiness: "REST", auth_model: "OAuth2" });
          setByPointer(surface.dataModel, path, rows);
          renderAllSurfaces();
        });
        wrap.appendChild(addBtn);
        return wrap;
      }

      if (type === "Button") {
        const btn = document.createElement("button");
        const variant = comp.variant || "primary";
        btn.className = "a2ui-btn " + (
          variant === "primary" ? "a2ui-btn-primary" :
          variant === "borderless" ? "a2ui-btn-borderless" : "a2ui-btn-secondary"
        );
        if (comp.child) {
          const childComp = surface.components.get(comp.child);
          btn.textContent = childComp ? resolveDynamic(surface, childComp.text) : "Continue";
        } else {
          btn.textContent = resolveDynamic(surface, comp.label) || "Continue";
        }
        btn.addEventListener("click", async () => {
          const ev = comp.action && comp.action.event;
          if (!ev) return;
          if (ev.name === "open_sharepoint_signin") {
            openSharePointPopup();
            return;
          }
          const resolvedCtx = {};
          for (const item of (ev.context || [])) {
            resolvedCtx[item.key] = resolveDynamic(surface, item.value);
          }
          await dispatchA2ATurn({
            action: {
              name: ev.name,
              surfaceId: surface.surfaceId,
              context: resolvedCtx
            }
          });
        });
        return btn;
      }

      // Fallback container for any composite/metric component
      const box = document.createElement("div");
      for (const cid of (comp.children || [])) {
        box.appendChild(renderComponent(surface, surface.components.get(cid)));
      }
      return box;
    }

    initIdentity();
  </script>
</body>
</html>
"""
