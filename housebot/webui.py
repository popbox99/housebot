"""Web UI and local dashboard for HouseBot.

Provides a standard-library-only web dashboard and setup wizard:
  * Hardware inspection and optimal LLM model recommendation
  * Obsidian vault auto-discovery and path selection
  * Messaging transport configuration (Web, Telegram, Signal)
  * Live interactive Web Chat playground (instant test-drive without external apps)
  * Mobile phone companion setup instructions (iOS & Android)
  * Auto-start background service management
"""

import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Tuple

from .config import DEFAULT_CONFIG_PATH, Config
from .hardware import inspect_hardware, recommend_model
from .service import install_service, is_service_installed, uninstall_service
from .wizard import detect_obsidian_vaults, validate_telegram_token

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HouseBot Dashboard & Setup</title>
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --card-border: #334155;
      --accent: #6366f1;
      --accent-hover: #4f46e5;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --radius: 12px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      display: flex;
      flex-direction: column;
      min-height: 100vh;
    }
    header {
      background: rgba(30, 41, 59, 0.85);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--card-border);
      padding: 16px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand-icon {
      font-size: 26px;
      background: linear-gradient(135deg, #6366f1, #a855f7);
      width: 44px;
      height: 44px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 10px;
    }
    .brand-title {
      font-size: 20px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      font-weight: 600;
      padding: 4px 10px;
      border-radius: 20px;
      background: #334155;
      color: var(--text-muted);
    }
    .badge-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--warning);
    }
    .badge-dot.active { background: var(--success); }
    nav {
      display: flex;
      gap: 8px;
      padding: 12px 24px;
      background: #111827;
      border-bottom: 1px solid var(--card-border);
      overflow-x: auto;
    }
    .nav-btn {
      background: transparent;
      border: 1px solid transparent;
      color: var(--text-muted);
      padding: 8px 16px;
      border-radius: 8px;
      font-weight: 600;
      font-size: 14px;
      cursor: pointer;
      transition: all 0.2s;
      white-space: nowrap;
    }
    .nav-btn:hover {
      color: var(--text);
      background: #1e293b;
    }
    .nav-btn.active {
      color: #fff;
      background: var(--accent);
    }
    main {
      flex: 1;
      max-width: 960px;
      width: 100%;
      margin: 0 auto;
      padding: 24px 16px;
    }
    .tab-content { display: none; }
    .tab-content.active { display: block; }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: var(--radius);
      padding: 24px;
      margin-bottom: 24px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }
    .card-title {
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .card-desc {
      color: var(--text-muted);
      font-size: 14px;
      margin-bottom: 20px;
    }
    .form-group {
      margin-bottom: 18px;
    }
    label {
      display: block;
      font-size: 13px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
      margin-bottom: 8px;
    }
    input[type="text"], input[type="password"], select, textarea {
      width: 100%;
      padding: 12px 14px;
      background: #0f172a;
      border: 1px solid var(--card-border);
      border-radius: 8px;
      color: var(--text);
      font-size: 14px;
      transition: border-color 0.2s;
    }
    input:focus, select:focus, textarea:focus {
      outline: none;
      border-color: var(--accent);
    }
    .btn {
      background: var(--accent);
      color: white;
      border: none;
      padding: 10px 20px;
      border-radius: 8px;
      font-weight: 600;
      font-size: 14px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: background 0.2s;
    }
    .btn:hover { background: var(--accent-hover); }
    .btn-secondary {
      background: #334155;
      color: var(--text);
    }
    .btn-secondary:hover { background: #475569; }
    .btn-success {
      background: var(--success);
    }
    .btn-success:hover { background: #059669; }
    .btn-sm {
      padding: 6px 12px;
      font-size: 13px;
    }
    .row {
      display: flex;
      gap: 12px;
      align-items: center;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }
    @media (max-width: 640px) {
      .grid-2 { grid-template-columns: 1fr; }
    }
    .stat-pill {
      background: #0f172a;
      border: 1px solid var(--card-border);
      padding: 12px 16px;
      border-radius: 8px;
    }
    .stat-label {
      font-size: 12px;
      color: var(--text-muted);
    }
    .stat-val {
      font-size: 16px;
      font-weight: 700;
      margin-top: 4px;
    }
    /* Chat Box */
    .chat-container {
      display: flex;
      flex-direction: column;
      height: 520px;
      background: #0f172a;
      border: 1px solid var(--card-border);
      border-radius: var(--radius);
      overflow: hidden;
    }
    .chat-messages {
      flex: 1;
      padding: 20px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .message {
      max-width: 80%;
      padding: 12px 16px;
      border-radius: 12px;
      font-size: 15px;
      line-height: 1.5;
      word-break: break-word;
    }
    .message.bot {
      align-self: flex-start;
      background: #1e293b;
      border: 1px solid var(--card-border);
      border-bottom-left-radius: 2px;
    }
    .message.user {
      align-self: flex-end;
      background: var(--accent);
      color: white;
      border-bottom-right-radius: 2px;
    }
    .chat-input-bar {
      padding: 14px;
      background: #1e293b;
      border-top: 1px solid var(--card-border);
      display: flex;
      gap: 10px;
    }
    .chat-input-bar input {
      flex: 1;
      padding: 10px 14px;
      background: #0f172a;
      border: 1px solid var(--card-border);
      border-radius: 8px;
      color: white;
      font-size: 15px;
    }
    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }
    .chip {
      background: #1e293b;
      border: 1px solid var(--card-border);
      padding: 6px 12px;
      border-radius: 20px;
      font-size: 12px;
      cursor: pointer;
      color: var(--text-muted);
      transition: all 0.2s;
    }
    .chip:hover {
      background: #334155;
      color: white;
      border-color: var(--accent);
    }
    .alert {
      padding: 12px 16px;
      border-radius: 8px;
      font-size: 14px;
      margin-bottom: 16px;
      display: none;
    }
    .alert-success { background: rgba(16, 185, 129, 0.2); border: 1px solid var(--success); color: #34d399; }
    .alert-error { background: rgba(239, 68, 68, 0.2); border: 1px solid var(--danger); color: #f87171; }
    .guide-step {
      display: flex;
      gap: 16px;
      margin-bottom: 18px;
      align-items: flex-start;
    }
    .step-num {
      width: 28px;
      height: 28px;
      background: var(--accent);
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      font-size: 14px;
      flex-shrink: 0;
    }
    .step-body {
      flex: 1;
      font-size: 14px;
    }
    .step-body h4 {
      font-size: 15px;
      margin-bottom: 4px;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-icon">🏠</div>
      <div>
        <div class="brand-title">HouseBot</div>
        <div class="badge" id="status-badge">
          <span class="badge-dot" id="status-dot"></span>
          <span id="status-text">Checking status...</span>
        </div>
      </div>
    </div>
    <div class="header-actions">
      <button class="btn btn-secondary btn-sm" onclick="fetchStatus()">↻ Refresh</button>
    </div>
  </header>

  <nav>
    <button class="nav-btn active" onclick="showTab('chat')">💬 Live Chat</button>
    <button class="nav-btn" onclick="showTab('setup')">⚙️ Setup Wizard</button>
    <button class="nav-btn" onclick="showTab('mobile')">📱 Mobile Companion</button>
    <button class="nav-btn" onclick="showTab('system')">🖥️ System & Hardware</button>
  </nav>

  <main>
    <div id="alert-box" class="alert"></div>

    <!-- TAB 1: LIVE CHAT -->
    <div id="tab-chat" class="tab-content active">
      <div class="card">
        <div class="card-title">💬 Live Bot Playground</div>
        <div class="card-desc">Talk with HouseBot instantly in your browser. No phone or chat apps required for testing.</div>

        <div class="chips">
          <span class="chip" onclick="sendSuggestion(this)">Remind me at 5pm to call mom</span>
          <span class="chip" onclick="sendSuggestion(this)">Add milk and organic sourdough to shopping list</span>
          <span class="chip" onclick="sendSuggestion(this)">What is on my shopping list?</span>
          <span class="chip" onclick="sendSuggestion(this)">Remember that the wifi password is SecretPassword123</span>
          <span class="chip" onclick="sendSuggestion(this)">What is the wifi password?</span>
          <span class="chip" onclick="sendSuggestion(this)">Note: Meeting went great today</span>
        </div>

        <div class="chat-container">
          <div class="chat-messages" id="chat-messages">
            <div class="message bot">
              👋 Hello! I am HouseBot, your local assistant. You can ask me to set reminders, manage your shopping list, take notes, or answer questions.
            </div>
          </div>
          <div class="chat-input-bar">
            <input type="text" id="chat-input" placeholder="Type a message or instruction..." onkeydown="if(event.key==='Enter') sendMessage()">
            <button class="btn" onclick="sendMessage()">Send</button>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 2: SETUP WIZARD -->
    <div id="tab-setup" class="tab-content">
      <div class="card">
        <div class="card-title">⚙️ Interactive Setup & Configuration</div>
        <div class="card-desc">Configure your storage, local AI brain, and messaging apps.</div>

        <!-- Quick Setup Tip: Install Telegram First -->
        <div style="background: linear-gradient(135deg, rgba(37, 99, 235, 0.15), rgba(59, 130, 246, 0.08)); border: 1px solid rgba(59, 130, 246, 0.4); border-radius: 8px; padding: 14px 18px; margin-bottom: 20px; display: flex; gap: 14px; align-items: center;">
          <div style="font-size: 28px; line-height: 1;">📱</div>
          <div style="flex: 1; font-size: 13.5px; line-height: 1.5;">
            <b style="color: #60a5fa; font-size: 14px;">💡 Pro-Tip: Install Telegram on your phone or PC before starting!</b><br>
            If you have Telegram open, getting your free bot token takes only 30 seconds by tapping 
            <a href="https://t.me/BotFather" target="_blank" style="color: #93c5fd; text-decoration: underline; font-weight: 600;">@BotFather</a>.<br>
            <span style="color: var(--text-muted);">Don't have Telegram yet? <a href="https://telegram.org" target="_blank" style="color: #93c5fd; text-decoration: underline;">Download Telegram</a> (iOS, Android, Windows, Mac).</span>
          </div>
        </div>

        <!-- Step 1: Storage -->
        <div class="form-group">
          <label>1. Notes & Tasks Storage (Obsidian Vault)</label>
          <div class="row">
            <select id="vault-select" onchange="onVaultChange()">
              <option value="">Scanning for Obsidian vaults...</option>
            </select>
          </div>
          <input type="text" id="custom-notes-dir" placeholder="Or enter custom folder path..." style="margin-top: 8px;">
          <small style="color: var(--text-muted); display: block; margin-top: 4px;">
            HouseBot stores markdown notes and shopping lists directly in your Obsidian vault or folder.
          </small>
        </div>

        <!-- Step 2: LLM Brain -->
        <div class="form-group">
          <label>2. Local AI Model (Hardware Recommended)</label>
          <div class="stat-pill" style="margin-bottom: 12px;">
            <div class="stat-label">Hardware Recommendation:</div>
            <div class="stat-val" id="rec-model-label" style="color: #818cf8;">Detecting system specs...</div>
            <div id="rec-model-reason" style="font-size: 13px; color: var(--text-muted); margin-top: 4px;"></div>
          </div>

          <div class="grid-2">
            <div>
              <label>Backend Provider</label>
              <select id="llm-provider" onchange="onProviderChange()">
                <option value="ollama">Ollama (Default: http://127.0.0.1:11434)</option>
                <option value="lmstudio">LM Studio (Default: http://127.0.0.1:1234)</option>
                <option value="llamacpp">llama.cpp (Default: http://127.0.0.1:8080)</option>
                <option value="openai">OpenAI / Cloud / Custom Base URL</option>
              </select>
            </div>
            <div>
              <label>Model Name</label>
              <input type="text" id="llm-model" value="llama3.2:3b">
            </div>
          </div>

          <div class="form-group" style="margin-top: 12px;">
            <label>Server Base URL</label>
            <input type="text" id="llm-base-url" value="http://127.0.0.1:11434">
          </div>

          <div class="form-group" id="api-key-group" style="display: none;">
            <label>API Key (Optional for local, required for Cloud)</label>
            <input type="password" id="llm-api-key" placeholder="sk-...">
          </div>

          <button class="btn btn-secondary btn-sm" onclick="testLlmConnection()">🔌 Test AI Connection</button>
          <span id="llm-test-result" style="margin-left: 10px; font-size: 13px;"></span>
        </div>

        <!-- Step 3: Messenger -->
        <div class="form-group">
          <label>3. Chat Messenger</label>
          <select id="transport-choice" onchange="onTransportChange()">
            <option value="web">Web Browser Only (Local testing)</option>
            <option value="telegram">Telegram Messenger (Recommended for phones)</option>
            <option value="signal">Signal Messenger (Advanced / signal-cli)</option>
          </select>
        </div>

        <div id="telegram-fields" style="display: none; background: #0f172a; padding: 16px; border-radius: 8px; margin-bottom: 18px; border: 1px solid var(--card-border);">
          <div class="form-group">
            <label>Telegram Bot Token</label>
            <input type="text" id="tg-token" placeholder="123456789:ABCdefGhIJKlmNoPQRstuVWXyz">
            <small style="color: var(--text-muted);">Get this by messaging <b>@BotFather</b> on Telegram and creating a new bot.</small>
          </div>
          <div class="form-group">
            <label>Your Telegram User ID (Allowlist security)</label>
            <input type="text" id="tg-user-id" placeholder="e.g. 123456789">
            <small style="color: var(--text-muted);">Message <b>@userinfobot</b> on Telegram to find your numeric ID.</small>
          </div>
          <button class="btn btn-secondary btn-sm" onclick="testTelegramToken()">✓ Verify Bot Token</button>
          <span id="tg-test-result" style="margin-left: 10px; font-size: 13px;"></span>
        </div>

        <div id="signal-fields" style="display: none; background: #0f172a; padding: 18px; border-radius: 8px; margin-bottom: 18px; border: 1px solid var(--card-border);">
          <div style="background: rgba(245, 158, 11, 0.15); border: 1px solid var(--warning); padding: 12px; border-radius: 6px; margin-bottom: 14px;">
            <div style="font-weight: 700; color: #fbbf24; font-size: 14px; margin-bottom: 4px;">⚠️ Do NOT use your personal cell phone number!</div>
            <div style="font-size: 13px; color: var(--text-muted);">
              Registering your personal cell number with a bot will de-register Signal on your phone. Instead, get a free dedicated number from Google Voice.
            </div>
          </div>

          <div style="background: #1e293b; padding: 14px; border-radius: 6px; margin-bottom: 14px; font-size: 13px;">
            <div style="font-weight: 700; color: #818cf8; margin-bottom: 6px;">💡 Step-by-Step Google Voice & Signal Guide:</div>
            <ol style="margin-left: 20px; line-height: 1.6; color: var(--text-muted);">
              <li>Go to <a href="https://voice.google.com" target="_blank" style="color: #818cf8; font-weight: 600;">voice.google.com</a> and claim a free US/Canada phone number.</li>
              <li>Register it in your terminal with signal-cli:<br><code style="background: #0f172a; padding: 2px 6px; border-radius: 4px; color: #e2e8f0;">signal-cli -u +1XXXXXXXXXX register</code></li>
              <li>Check your Google Voice inbox for the 6-digit SMS verification code.</li>
              <li>Verify it in your terminal:<br><code style="background: #0f172a; padding: 2px 6px; border-radius: 4px; color: #e2e8f0;">signal-cli -u +1XXXXXXXXXX verify 123-456</code></li>
              <li>Start the background daemon:<br><code style="background: #0f172a; padding: 2px 6px; border-radius: 4px; color: #e2e8f0;">signal-cli --socket ~/.local/run/signal-cli/socket daemon</code></li>
            </ol>
            <div style="margin-top: 10px;">
              <a href="https://voice.google.com" target="_blank" class="btn btn-secondary btn-sm" style="text-decoration: none;">↗ Open Google Voice</a>
            </div>
          </div>

          <div class="form-group">
            <label>Bot's Signal Phone Number (Google Voice Number)</label>
            <input type="text" id="signal-account" placeholder="+15555550100">
          </div>
          <div class="form-group">
            <label>Your Personal Phone Number (Allowlist Security)</label>
            <input type="text" id="signal-user-id" placeholder="+15555550199">
            <small style="color: var(--text-muted);">Only messages sent from this phone number will be accepted.</small>
          </div>
          <div class="form-group">
            <label>signal-cli Socket Path</label>
            <input type="text" id="signal-socket" value="~/.local/run/signal-cli/socket">
          </div>
        </div>

        <!-- Step 4: Home Assistant (Optional) -->
        <div class="form-group" style="border-top: 1px solid var(--card-border); padding-top: 18px; margin-top: 18px;">
          <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; text-transform: none; font-size: 14px;">
            <input type="checkbox" id="ha-enabled" onchange="onHaToggle()" style="width: 18px; height: 18px; accent-color: var(--accent);">
            <span><b>4. Connect to Home Assistant (Optional)</b></span>
          </label>
          <small style="color: var(--text-muted); display: block; margin-top: 4px;">
            Connect your smart home to enable location-based reminders ("remind me when I get home"), vacuum control, and voice satellites.
          </small>
        </div>

        <div id="ha-fields" style="display: none; background: #0f172a; padding: 18px; border-radius: 8px; margin-bottom: 18px; border: 1px solid var(--card-border);">
          <div class="form-group">
            <label>Home Assistant URL</label>
            <input type="text" id="ha-url" value="http://homeassistant.local:8123">
          </div>
          <div class="form-group">
            <label>Long-Lived Access Token</label>
            <input type="password" id="ha-token" placeholder="Paste your Long-Lived Access Token...">
            <small style="color: var(--text-muted);">In Home Assistant: Click your Profile icon at bottom-left &rarr; Security &rarr; Create Long-Lived Access Token.</small>
          </div>
          <div class="grid-2">
            <div>
              <label>Person Entity (For Location Reminders)</label>
              <input type="text" id="ha-person" placeholder="person.matt">
            </div>
            <div>
              <label>Vacuum Entity</label>
              <input type="text" id="ha-vacuum" value="vacuum.robot">
            </div>
          </div>
          <div style="margin-top: 12px;">
            <button class="btn btn-secondary btn-sm" onclick="testHaConnection()">🏠 Test Home Assistant Connection</button>
            <span id="ha-test-result" style="margin-left: 10px; font-size: 13px;"></span>
          </div>

          <div style="background: #1e293b; padding: 12px; border-radius: 6px; margin-top: 16px; font-size: 13px;">
            <div style="font-weight: 700; color: #818cf8; margin-bottom: 4px;">🎙️ Use HouseBot as Home Assistant's Voice Brain (Assist):</div>
            <p style="color: var(--text-muted); line-height: 1.5;">
              In Home Assistant: Go to <b>Settings &rarr; Devices & Services &rarr; Add Integration &rarr; OpenAI Conversation</b>.<br>
              Server URL: <code style="color: white; background: #0f172a; padding: 2px 5px; border-radius: 3px;">http://&lt;your-computer-ip&gt;:8082/v1</code> &bull; API Key: <code style="color: white; background: #0f172a; padding: 2px 5px; border-radius: 3px;">housebot-local</code><br>
              Your smart speakers and Assist microphones will now speak directly with HouseBot!
            </p>
          </div>
        </div>

        <!-- Save Button -->
        <div style="margin-top: 24px;">
          <button class="btn btn-success" onclick="saveConfiguration()">💾 Save & Launch HouseBot</button>
        </div>
      </div>
    </div>

    <!-- TAB 3: MOBILE COMPANION -->
    <div id="tab-mobile" class="tab-content">
      <div class="card">
        <div class="card-title">📱 Mobile Companion Setup Guide</div>
        <div class="card-desc">Talk to HouseBot seamlessly on the go from your phone.</div>

        <div class="guide-step">
          <div class="step-num">1</div>
          <div class="step-body">
            <h4>Primary Messenger (Telegram or Signal)</h4>
            <p style="color: var(--text-muted);">
              <b>For Telegram:</b> Download Telegram on your <a href="https://apps.apple.com/app/telegram-messenger/id686449807" target="_blank" style="color: #818cf8;">iPhone (App Store)</a> or <a href="https://play.google.com/store/apps/details?id=org.telegram.messenger" target="_blank" style="color: #818cf8;">Android (Play Store)</a>. Search for your bot username and tap <b>START</b>.<br><br>
              <b>For Signal:</b> Download Signal on <a href="https://apps.apple.com/app/signal-private-messenger/id874135377" target="_blank" style="color: #818cf8;">iPhone</a> or <a href="https://play.google.com/store/apps/details?id=org.thoughtcrime.securesms" target="_blank" style="color: #818cf8;">Android</a>. Add your bot's Google Voice number to your phone contacts as <i>"HouseBot"</i>, then message it directly!
            </p>
          </div>
        </div>

        <div class="guide-step">
          <div class="step-num">2</div>
          <div class="step-body">
            <h4>Obsidian Mobile Sync (Optional)</h4>
            <p style="color: var(--text-muted);">If you use Obsidian on your phone, enable <b>Obsidian Sync</b> or use iCloud / Syncthing so all notes, reminders, and lists created by HouseBot sync live to your phone.</p>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 4: SYSTEM & HARDWARE -->
    <div id="tab-system" class="tab-content">
      <div class="card">
        <div class="card-title">🖥️ Hardware & Background Daemon</div>
        <div class="card-desc">Hardware specs probed directly from this system without external dependencies.</div>

        <div class="grid-2">
          <div class="stat-pill">
            <div class="stat-label">RAM Available</div>
            <div class="stat-val" id="hw-ram">Scanning...</div>
          </div>
          <div class="stat-pill">
            <div class="stat-label">CPU Cores</div>
            <div class="stat-val" id="hw-cpu">Scanning...</div>
          </div>
          <div class="stat-pill">
            <div class="stat-label">GPU / Acceleration</div>
            <div class="stat-val" id="hw-gpu">Scanning...</div>
          </div>
          <div class="stat-pill">
            <div class="stat-label">Detected Local AI Ports</div>
            <div class="stat-val" id="hw-ports">Scanning...</div>
          </div>
        </div>

        <div style="margin-top: 24px; border-top: 1px solid var(--card-border); padding-top: 20px;">
          <div class="card-title">🔄 Background Service (Auto-Start on Boot)</div>
          <div class="card-desc">Keep HouseBot running quietly in the background so it is always ready to receive messages.</div>
          <div class="row">
            <button class="btn btn-secondary" id="btn-service" onclick="toggleService()">Manage Service</button>
            <span id="service-status-text" style="color: var(--text-muted); font-size: 14px;"></span>
          </div>
        </div>

        <div style="margin-top: 24px; border-top: 1px solid var(--card-border); padding-top: 20px;">
          <div class="card-title" style="color: var(--danger);">🗑️ Uninstall HouseBot</div>
          <div class="card-desc">Stop all background services, remove desktop shortcuts, and optionally clear local configuration. Your Obsidian notes and vaults are never touched.</div>
          <div class="row">
            <button class="btn btn-danger" onclick="confirmUninstall()">Uninstall HouseBot</button>
            <span id="uninstall-status-text" style="color: var(--text-muted); font-size: 14px;"></span>
          </div>
        </div>
      </div>
    </div>
  </main>

  <script>
    let serviceInstalled = false;

    function showTab(name) {
      document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));
      const tab = document.getElementById('tab-' + name);
      if (tab) tab.classList.add('active');
      event.target.classList.add('active');
    }

    function showAlert(msg, isError = false) {
      const box = document.getElementById('alert-box');
      box.className = 'alert ' + (isError ? 'alert-error' : 'alert-success');
      box.textContent = msg;
      box.style.display = 'block';
      setTimeout(() => { box.style.display = 'none'; }, 5000);
    }

    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        const dot = document.getElementById('status-dot');
        const text = document.getElementById('status-text');
        serviceInstalled = data.service_installed;

        if (data.configured) {
          dot.className = 'badge-dot active';
          text.textContent = 'Active (' + data.bot_name + ')';
        } else {
          dot.className = 'badge-dot';
          text.textContent = 'Setup Mode (Not Configured)';
        }

        const srvBtn = document.getElementById('btn-service');
        const srvText = document.getElementById('service-status-text');
        if (serviceInstalled) {
          srvBtn.textContent = 'Disable Auto-Start';
          srvBtn.className = 'btn btn-secondary';
          srvText.textContent = '✓ Currently installed and will start automatically on boot.';
        } else {
          srvBtn.textContent = 'Enable Auto-Start on Boot';
          srvBtn.className = 'btn btn-secondary';
          srvText.textContent = 'Not currently configured to auto-start.';
        }
      } catch (e) {
        console.error('fetchStatus failed:', e);
      }
    }

    async function loadHardware() {
      try {
        const res = await fetch('/api/hardware');
        const data = await res.json();
        const p = data.profile;
        const r = data.recommendation;

        document.getElementById('hw-ram').textContent = (p.ram_gb || 0) + ' GB';
        document.getElementById('hw-cpu').textContent = (p.cpu_cores || 1) + ' Cores';
        document.getElementById('hw-gpu').textContent = p.gpu ? (p.gpu.model + ' (' + p.gpu.vram_gb + ' GB)') : (p.apple_silicon ? 'Apple Silicon' : 'CPU Only');
        document.getElementById('hw-ports').textContent = (p.detected_ports && p.detected_ports.length) ? p.detected_ports.join(', ') : 'None active';

        if (r && r.recommended_model) {
          document.getElementById('rec-model-label').textContent = r.recommended_model;
          document.getElementById('rec-model-reason').textContent = r.reason || '';
          document.getElementById('llm-model').value = r.recommended_model;
        }
      } catch (e) {
        console.error('loadHardware failed:', e);
      }
    }

    async function loadVaults() {
      try {
        const res = await fetch('/api/vaults');
        const data = await res.json();
        const sel = document.getElementById('vault-select');
        sel.innerHTML = '';

        if (data.vaults && data.vaults.length > 0) {
          data.vaults.forEach((v, idx) => {
            const opt = document.createElement('option');
            opt.value = v;
            opt.textContent = 'Obsidian Vault: ' + v;
            sel.appendChild(opt);
          });
        }
        const customOpt = document.createElement('option');
        customOpt.value = data.default_notes || '';
        customOpt.textContent = 'Default Notes Directory (' + (data.default_notes || '~/Documents/Notes') + ')';
        sel.appendChild(customOpt);
        onVaultChange();
      } catch (e) {
        console.error('loadVaults failed:', e);
      }
    }

    function onVaultChange() {
      const sel = document.getElementById('vault-select');
      document.getElementById('custom-notes-dir').value = sel.value;
    }

    function onProviderChange() {
      const p = document.getElementById('llm-provider').value;
      const urlInput = document.getElementById('llm-base-url');
      const keyGroup = document.getElementById('api-key-group');

      if (p === 'ollama') {
        urlInput.value = 'http://127.0.0.1:11434';
        keyGroup.style.display = 'none';
      } else if (p === 'lmstudio') {
        urlInput.value = 'http://127.0.0.1:1234';
        keyGroup.style.display = 'none';
      } else if (p === 'llamacpp') {
        urlInput.value = 'http://127.0.0.1:8080';
        keyGroup.style.display = 'none';
      } else if (p === 'openai') {
        urlInput.value = 'https://api.openai.com/v1';
        keyGroup.style.display = 'block';
      }
    }

    function onTransportChange() {
      const t = document.getElementById('transport-choice').value;
      document.getElementById('telegram-fields').style.display = (t === 'telegram') ? 'block' : 'none';
      document.getElementById('signal-fields').style.display = (t === 'signal') ? 'block' : 'none';
    }

    async function testLlmConnection() {
      const resEl = document.getElementById('llm-test-result');
      resEl.textContent = 'Testing connection...';
      resEl.style.color = 'var(--text-muted)';

      try {
        const payload = {
          base_url: document.getElementById('llm-base-url').value,
          api: (document.getElementById('llm-provider').value === 'ollama') ? 'ollama' : 'openai',
          model: document.getElementById('llm-model').value,
          api_key: document.getElementById('llm-api-key').value
        };
        const res = await fetch('/api/test-llm', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.ok) {
          resEl.textContent = '✓ ' + (data.message || 'Connected successfully!');
          resEl.style.color = 'var(--success)';
        } else {
          resEl.textContent = '✗ ' + (data.error || 'Failed to connect');
          resEl.style.color = 'var(--danger)';
        }
      } catch (e) {
        resEl.textContent = '✗ Error: ' + e.message;
        resEl.style.color = 'var(--danger)';
      }
    }

    async function testTelegramToken() {
      const resEl = document.getElementById('tg-test-result');
      resEl.textContent = 'Verifying token with Telegram...';
      resEl.style.color = 'var(--text-muted)';

      const token = document.getElementById('tg-token').value.trim();
      if (!token) {
        resEl.textContent = 'Please enter a token first';
        resEl.style.color = 'var(--warning)';
        return;
      }

      try {
        const res = await fetch('/api/test-telegram', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({token})
        });
        const data = await res.json();
        if (data.ok) {
          resEl.textContent = '✓ Valid! Bot: @' + (data.bot_username || 'bot');
          resEl.style.color = 'var(--success)';
        } else {
          resEl.textContent = '✗ ' + (data.error || 'Invalid token');
          resEl.style.color = 'var(--danger)';
        }
      } catch (e) {
        resEl.textContent = '✗ ' + e.message;
        resEl.style.color = 'var(--danger)';
      }
    }

    function onHaToggle() {
      const enabled = document.getElementById('ha-enabled').checked;
      document.getElementById('ha-fields').style.display = enabled ? 'block' : 'none';
    }

    async function testHaConnection() {
      const resEl = document.getElementById('ha-test-result');
      resEl.textContent = 'Testing connection to Home Assistant...';
      resEl.style.color = 'var(--text-muted)';

      const url = document.getElementById('ha-url').value.trim();
      const token = document.getElementById('ha-token').value.trim();
      if (!token) {
        resEl.textContent = 'Please enter an access token first';
        resEl.style.color = 'var(--warning)';
        return;
      }

      try {
        const res = await fetch('/api/test-ha', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({base_url: url, token: token})
        });
        const data = await res.json();
        if (data.ok) {
          resEl.textContent = '✓ ' + (data.message || 'Connected successfully!');
          resEl.style.color = 'var(--success)';
        } else {
          resEl.textContent = '✗ ' + (data.error || 'Connection failed');
          resEl.style.color = 'var(--danger)';
        }
      } catch (e) {
        resEl.textContent = '✗ ' + e.message;
        resEl.style.color = 'var(--danger)';
      }
    }

    async function saveConfiguration() {
      const notesDir = document.getElementById('custom-notes-dir').value || '~/Documents/Notes';
      const provider = document.getElementById('llm-provider').value;
      const baseUrl = document.getElementById('llm-base-url').value;
      const model = document.getElementById('llm-model').value;
      const apiKey = document.getElementById('llm-api-key').value;
      const transportChoice = document.getElementById('transport-choice').value;

      const payload = {
        notes_dir: notesDir,
        llm: {
          base_url: baseUrl,
          model: model,
          api: (provider === 'ollama') ? 'ollama' : 'openai',
          api_key: apiKey
        },
        transport: transportChoice,
        telegram: {
          token: document.getElementById('tg-token').value.trim(),
          user_id: document.getElementById('tg-user-id').value.trim()
        },
        signal: {
          account: document.getElementById('signal-account').value.trim(),
          user_id: document.getElementById('signal-user-id').value.trim(),
          socket: document.getElementById('signal-socket').value.trim()
        },
        homeassistant: {
          enabled: document.getElementById('ha-enabled').checked,
          base_url: document.getElementById('ha-url').value.trim(),
          token: document.getElementById('ha-token').value.trim(),
          person_entity: document.getElementById('ha-person').value.trim(),
          vacuum_entity: document.getElementById('ha-vacuum').value.trim()
        }
      };

      try {
        const res = await fetch('/api/save-config', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.ok) {
          showAlert('✓ Configuration saved! HouseBot is ready.');
          fetchStatus();
          showTab('chat');
        } else {
          showAlert('✗ Failed to save: ' + (data.error || 'Unknown error'), true);
        }
      } catch (e) {
        showAlert('✗ Error saving: ' + e.message, true);
      }
    }

    async function toggleService() {
      const action = serviceInstalled ? 'uninstall' : 'install';
      try {
        const res = await fetch('/api/service', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action})
        });
        const data = await res.json();
        showAlert(data.message, !data.ok);
        fetchStatus();
      } catch (e) {
        showAlert('Error: ' + e.message, true);
      }
    }

    async function confirmUninstall() {
      if (!confirm("Are you sure you want to uninstall HouseBot?\\n\\nThis will stop background services and remove application files. (Your personal notes and Obsidian vaults will NOT be touched).")) {
        return;
      }
      const removeConfig = confirm("Do you also want to delete your HouseBot configuration (~/.config/housebot)?\\n\\nClick OK to delete configuration, or Cancel to keep your settings.");

      const statusEl = document.getElementById('uninstall-status-text');
      statusEl.textContent = 'Uninstalling HouseBot...';
      statusEl.style.color = 'var(--text-muted)';

      try {
        const res = await fetch('/api/uninstall', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({remove_config: removeConfig, remove_data: removeConfig})
        });
        const data = await res.json();
        if (data.ok) {
          statusEl.textContent = '✓ Uninstalled successfully. You can now close this tab.';
          statusEl.style.color = 'var(--success)';
          showAlert('HouseBot uninstalled successfully.', false);
        } else {
          statusEl.textContent = '✗ Error: ' + (data.error || 'Failed');
          statusEl.style.color = 'var(--danger)';
        }
      } catch (e) {
        statusEl.textContent = '✗ ' + e.message;
        statusEl.style.color = 'var(--danger)';
      }
    }

    function sendSuggestion(el) {
      document.getElementById('chat-input').value = el.textContent;
      sendMessage();
    }

    async function sendMessage() {
      const input = document.getElementById('chat-input');
      const text = input.value.trim();
      if (!text) return;

      const chatBox = document.getElementById('chat-messages');

      // Append user bubble
      const userDiv = document.createElement('div');
      userDiv.className = 'message user';
      userDiv.textContent = text;
      chatBox.appendChild(userDiv);
      input.value = '';
      chatBox.scrollTop = chatBox.scrollHeight;

      // Thinking indicator
      const botDiv = document.createElement('div');
      botDiv.className = 'message bot';
      botDiv.textContent = '...';
      chatBox.appendChild(botDiv);
      chatBox.scrollTop = chatBox.scrollHeight;

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({message: text})
        });
        const data = await res.json();
        botDiv.textContent = data.reply || '(Done)';
      } catch (e) {
        botDiv.textContent = '⚠️ Error: ' + e.message;
      }
      chatBox.scrollTop = chatBox.scrollHeight;
    }

    async function loadExistingConfig() {
      try {
        const res = await fetch('/api/config');
        const cfg = await res.json();
        if (!cfg || !Object.keys(cfg).length) return;

        // Notes dir
        if (cfg.skills && cfg.skills.notes_dir) {
          const notesInput = document.getElementById('custom-notes-dir');
          if (notesInput) notesInput.value = cfg.skills.notes_dir;
        }

        // LLM settings
        if (cfg.llm && cfg.llm.backends && cfg.llm.backends.length > 0) {
          const b = cfg.llm.backends[0];
          if (b.api) {
            const provSelect = document.getElementById('llm-provider');
            if (b.api === 'ollama') provSelect.value = 'ollama';
            else if (b.base_url && b.base_url.includes('1234')) provSelect.value = 'lmstudio';
            else if (b.base_url && b.base_url.includes('openai')) provSelect.value = 'openai';
            else provSelect.value = 'ollama';
          }
          if (b.base_url) document.getElementById('llm-base-url').value = b.base_url;
          if (b.model) document.getElementById('llm-model').value = b.model;
          if (b.api_key) {
            document.getElementById('llm-api-key').value = b.api_key;
            document.getElementById('api-key-group').style.display = 'block';
          }
        }

        // Transports
        if (cfg.transports) {
          if (cfg.transports.telegram && cfg.transports.telegram.enabled) {
            document.getElementById('transport-choice').value = 'telegram';
            document.getElementById('tg-token').value = cfg.transports.telegram.token || '';
            if (cfg.bot && cfg.bot.allowed_senders && cfg.bot.allowed_senders.length) {
              document.getElementById('tg-user-id').value = cfg.bot.allowed_senders[0];
            }
          } else if (cfg.transports.signal && cfg.transports.signal.enabled) {
            document.getElementById('transport-choice').value = 'signal';
            document.getElementById('signal-account').value = cfg.transports.signal.account || '';
            document.getElementById('signal-socket').value = cfg.transports.signal.socket || '';
            if (cfg.bot && cfg.bot.allowed_senders && cfg.bot.allowed_senders.length) {
              document.getElementById('signal-user-id').value = cfg.bot.allowed_senders[0];
            }
          }
          onTransportChange();
        }

        // Home Assistant
        if (cfg.skills && cfg.skills.homeassistant) {
          const ha = cfg.skills.homeassistant;
          if (ha.enabled) {
            document.getElementById('ha-enabled').checked = true;
            document.getElementById('ha-fields').style.display = 'block';
            if (ha.base_url) document.getElementById('ha-url').value = ha.base_url;
            if (ha.token) document.getElementById('ha-token').value = ha.token;
            if (ha.person_entity) document.getElementById('ha-person').value = ha.person_entity;
            if (ha.vacuum_entity) document.getElementById('ha-vacuum').value = ha.vacuum_entity;
          }
        }
      } catch (e) {
        console.error('loadExistingConfig failed:', e);
      }
    }

    // Initialize on page load
    window.onload = function() {
      fetchStatus();
      loadHardware();
      loadVaults();
      loadExistingConfig();
    };
  </script>
</body>
</html>
"""


class WebUIHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for the HouseBot dashboard and REST APIs."""

    def log_message(self, format, *args):
        # Silence default stderr logging for clean output
        pass

    @property
    def server_instance(self):
        return self.server

    def _send_json(self, status: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html", "/setup", "/chat"):
            data = HTML_DASHBOARD.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/status":
            cfg_exists = os.path.exists(DEFAULT_CONFIG_PATH)
            bot_name = "HouseBot"
            if self.server_instance.cfg:
                bot_name = self.server_instance.cfg.get("bot", {}).get("name", "HouseBot")
            self._send_json(200, {
                "configured": cfg_exists,
                "bot_name": bot_name,
                "service_installed": is_service_installed(),
                "version": "0.2.0",
            })
            return

        if path == "/api/hardware":
            hw = inspect_hardware()
            rec = recommend_model(hw)
            self._send_json(200, {
                "profile": hw.to_dict(),
                "recommendation": rec,
            })
            return

        if path == "/api/vaults":
            vaults = [str(v) for v in detect_obsidian_vaults()]
            default_notes = str(Path.home() / "Documents" / "Notes")
            self._send_json(200, {
                "vaults": vaults,
                "default_notes": default_notes,
            })
            return

        if path == "/api/config":
            cfg = self.server_instance.cfg
            if not cfg and os.path.exists(DEFAULT_CONFIG_PATH):
                try:
                    cfg = Config(DEFAULT_CONFIG_PATH)
                except Exception:
                    pass
            self._send_json(200, cfg.to_dict() if cfg else {})
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_json()

        if path == "/api/test-llm":
            base_url = body.get("base_url", "http://127.0.0.1:11434")
            api_type = body.get("api", "ollama")
            model = body.get("model", "llama3.2:3b")
            api_key = body.get("api_key", "")

            # Probe server
            endpoint = f"{base_url}/api/tags" if api_type == "ollama" else f"{base_url}/models"
            if not endpoint.startswith("http"):
                endpoint = f"http://{endpoint}"

            headers = {"User-Agent": "HouseBot/0.2.0"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            req = urllib.request.Request(endpoint, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    if resp.status in (200, 204):
                        self._send_json(200, {
                            "ok": True,
                            "message": f"Connected to {api_type} at {base_url}!",
                        })
                        return
            except Exception as e:
                self._send_json(200, {
                    "ok": False,
                    "error": f"Failed to connect to {base_url}: {e}",
                })
                return

        if path == "/api/test-telegram":
            token = body.get("token", "").strip()
            ok, meta = validate_telegram_token(token)
            if ok:
                self._send_json(200, {
                    "ok": True,
                    "bot_username": meta.get("bot_username", ""),
                    "first_name": meta.get("first_name", ""),
                })
            else:
                self._send_json(200, {
                    "ok": False,
                    "error": meta.get("error", "Invalid Telegram token"),
                })
            return

        if path == "/api/test-ha":
            base_url = (body.get("base_url") or "").rstrip("/")
            token = (body.get("token") or "").strip()
            if not base_url:
                self._send_json(200, {"ok": False, "error": "Home Assistant URL cannot be empty"})
                return
            if not base_url.startswith("http"):
                base_url = f"http://{base_url}"
            endpoint = f"{base_url}/api/"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "HouseBot/0.2.0",
            }
            req = urllib.request.Request(endpoint, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=4.0) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        msg = data.get("message", "API running.")
                        self._send_json(200, {"ok": True, "message": f"Connected to Home Assistant! ({msg})"})
                        return
                    else:
                        self._send_json(200, {"ok": False, "error": f"Home Assistant returned status {resp.status}"})
                        return
            except urllib.error.HTTPError as e:
                self._send_json(200, {"ok": False, "error": f"HTTP {e.code}: {e.reason} (Check your Long-Lived Token)"})
                return
            except Exception as e:
                self._send_json(200, {"ok": False, "error": f"Failed to connect to {base_url}: {e}"})
                return

        if path == "/api/chat":
            msg = body.get("message", "").strip()
            engine = self.server_instance.engine
            if not engine:
                # Lazy-init engine if possible
                try:
                    cfg = Config()
                    from .engine import Engine
                    engine = Engine(cfg)
                    self.server_instance.engine = engine
                except Exception as e:
                    self._send_json(200, {"reply": f"Bot engine not active: {e}"})
                    return

            try:
                reply, _ = engine.handle("web", msg)
                self._send_json(200, {"reply": reply or "(Empty reply)"})
            except Exception as e:
                self._send_json(200, {"reply": f"Error handling message: {e}"})
            return

        if path == "/api/save-config":
            try:
                notes_dir = body.get("notes_dir") or str(Path.home() / "Documents" / "Notes")
                llm_info = body.get("llm", {})
                transport_choice = body.get("transport", "web")
                tg_info = body.get("telegram", {})
                signal_info = body.get("signal", {})

                allowed_senders = []
                transports = {
                    "signal": {"enabled": False},
                    "telegram": {"enabled": False, "token": ""},
                    "api": {"enabled": True, "port": self.server_instance.server_port, "token": "housebot-local"},
                }

                if transport_choice == "telegram":
                    token = tg_info.get("token", "").strip()
                    user_id = tg_info.get("user_id", "").strip()
                    transports["telegram"] = {"enabled": True, "token": token}
                    if user_id:
                        allowed_senders.append(user_id)
                elif transport_choice == "signal":
                    account = signal_info.get("account", "").strip()
                    user_id = signal_info.get("user_id", "").strip()
                    socket_path = signal_info.get("socket", "").strip() or "~/.local/run/signal-cli/socket"
                    transports["signal"] = {"enabled": True, "account": account, "socket": socket_path}
                    if user_id:
                        allowed_senders.append(user_id)

                backends = [{
                    "name": "default",
                    "base_url": llm_info.get("base_url") or "http://127.0.0.1:11434",
                    "model": llm_info.get("model") or "llama3.2:3b",
                    "api": llm_info.get("api") or "ollama",
                }]
                if llm_info.get("api_key"):
                    backends[0]["api_key"] = llm_info.get("api_key")

                ha_info = body.get("homeassistant", {})
                skills_cfg = {
                    "notes_dir": notes_dir,
                    "search_dirs": [notes_dir],
                }
                if ha_info.get("enabled"):
                    skills_cfg["homeassistant"] = {
                        "enabled": True,
                        "base_url": (ha_info.get("base_url") or "http://homeassistant.local:8123").rstrip("/"),
                        "token": ha_info.get("token", "").strip(),
                        "person_entity": ha_info.get("person_entity", "").strip(),
                        "vacuum_entity": ha_info.get("vacuum_entity", "vacuum.robot").strip(),
                        "zones": {"home": "home"},
                    }
                elif "enabled" in ha_info and not ha_info.get("enabled"):
                    skills_cfg["homeassistant"] = {"enabled": False}

                target_cfg = Path(DEFAULT_CONFIG_PATH)
                existing = {}
                if target_cfg.exists():
                    try:
                        with open(target_cfg, "r", encoding="utf-8") as f:
                            existing = json.load(f)
                    except Exception:
                        pass

                if "skills" in existing and isinstance(existing["skills"], dict):
                    merged_skills = dict(existing["skills"])
                    merged_skills.update(skills_cfg)
                    skills_cfg = merged_skills

                config_data = {
                    "bot": {
                        "name": "HouseBot",
                        "data_dir": str(Path.home() / ".local" / "share" / "housebot"),
                        "allowed_senders": allowed_senders,
                    },
                    "llm": {"backends": backends},
                    "skills": skills_cfg,
                    "transports": transports,
                }

                target_cfg.parent.mkdir(parents=True, exist_ok=True)
                with open(target_cfg, "w", encoding="utf-8") as f:
                    json.dump(config_data, f, indent=2, ensure_ascii=False)

                # Reload server engine & config
                new_cfg = Config(str(target_cfg))
                self.server_instance.cfg = new_cfg
                from .engine import Engine
                self.server_instance.engine = Engine(new_cfg)

                self._send_json(200, {"ok": True, "message": "Config saved successfully!"})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/service":
            action = body.get("action", "")
            if action == "install":
                ok, msg = install_service()
                self._send_json(200, {"ok": ok, "message": msg})
            elif action == "uninstall":
                ok, msg = uninstall_service()
                self._send_json(200, {"ok": ok, "message": msg})
            else:
                self._send_json(400, {"ok": False, "message": "Invalid action"})
            return

        if path == "/api/uninstall":
            from .uninstaller import perform_uninstall
            rem_cfg = bool(body.get("remove_config", False))
            rem_data = bool(body.get("remove_data", False))
            ok, logs = perform_uninstall(remove_config=rem_cfg, remove_data=rem_data)
            self._send_json(200, {"ok": ok, "logs": logs, "message": "HouseBot uninstalled successfully."})
            return

        self.send_response(404)
        self.end_headers()


class WebUIServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying HouseBot configuration and engine references."""

    def __init__(self, host: str, port: int, cfg=None, engine=None):
        super().__init__((host, port), WebUIHandler)
        self.cfg = cfg
        self.engine = engine


def find_available_port(start_port: int = 8082, max_attempts: int = 10) -> int:
    """Find an available TCP port starting from start_port."""
    for p in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    # Fallback to OS assigned ephemeral port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_webui(
    cfg=None,
    engine=None,
    port: int = 8082,
    open_browser: bool = True,
    in_background: bool = False,
) -> Tuple[WebUIServer, int, Optional[threading.Thread]]:
    """Start the HouseBot Web UI server.

    If in_background is True, runs in a daemon thread and returns (server, port, thread).
    Otherwise blocks serving requests.
    """
    actual_port = find_available_port(port)
    server = WebUIServer("127.0.0.1", actual_port, cfg=cfg, engine=engine)

    url = f"http://127.0.0.1:{actual_port}"
    print(f"[housebot] Web dashboard & setup available at: {url}")

    if open_browser:
        def _open():
            time.sleep(0.3)
            try:
                webbrowser.open_new_tab(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True, name="browser-launcher").start()

    if in_background:
        t = threading.Thread(target=server.serve_forever, daemon=True, name="webui")
        t.start()
        return server, actual_port, t
    else:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return server, actual_port, None
