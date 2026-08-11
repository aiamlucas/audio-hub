import subprocess
from flask import Flask, jsonify

app = Flask(__name__)
SCRIPT = "/usr/local/bin/audio-mode.sh"

INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Audio Hub</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&family=Roboto+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #1b1c1e;
      --panel: #232427;
      --panel-edge: #2d2e31;
      --hairline: #3a3b3f;
      --text: #e9e7e2;
      --text-dim: #8b8d92;
      --led-off: #4a4b4f;
      --led-on: #e8a33d;
      --led-on-glow: rgba(232, 163, 61, 0.55);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: var(--bg);
      background-image: radial-gradient(circle at 50% 0%, rgba(255,255,255,0.03), transparent 60%);
      font-family: 'Roboto', -apple-system, sans-serif;
      color: var(--text);
      padding: 2rem 1rem;
    }
    .unit {
      width: 100%;
      max-width: 460px;
      background: var(--panel);
      border: 1px solid var(--panel-edge);
      border-radius: 10px;
      box-shadow:
        0 1px 0 rgba(255,255,255,0.04) inset,
        0 20px 40px rgba(0,0,0,0.35);
      padding: 1.75rem 1.5rem 2rem;
    }
    .readout {
      font-family: 'Roboto Mono', monospace;
      font-size: 0.7rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--text-dim);
      border-bottom: 1px solid var(--hairline);
      padding-bottom: 0.9rem;
      margin-bottom: 1.4rem;
    }
    h1 {
      font-size: 1.5rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      margin: 0 0 1.6rem;
    }
    .modes {
      display: flex;
      flex-direction: column;
      gap: 0.6rem;
    }
    .mode-btn {
      display: flex;
      align-items: center;
      gap: 0.8rem;
      width: 100%;
      text-align: left;
      font-family: 'Roboto', sans-serif;
      font-size: 0.95rem;
      font-weight: 500;
      letter-spacing: 0.02em;
      color: var(--text);
      background: #1e1f22;
      border: 1px solid var(--hairline);
      border-radius: 7px;
      padding: 0.85rem 1rem;
      cursor: pointer;
      transition: border-color 0.15s ease, background 0.15s ease;
    }
    .mode-btn:hover { border-color: #55565b; }
    .mode-btn:focus-visible { outline: 2px solid var(--led-on); outline-offset: 2px; }
    .led {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--led-off);
      flex-shrink: 0;
      transition: background 0.2s ease, box-shadow 0.2s ease;
    }
    .mode-btn.active {
      border-color: var(--led-on);
      background: #26221a;
    }
    .mode-btn.active .led {
      background: var(--led-on);
      box-shadow: 0 0 8px 2px var(--led-on-glow);
    }
    .status-line {
      margin-top: 1.5rem;
      padding-top: 1rem;
      border-top: 1px solid var(--hairline);
      font-family: 'Roboto Mono', monospace;
      font-size: 0.72rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-dim);
    }
    .status-line strong {
      color: var(--text);
      font-weight: 500;
    }
    @media (prefers-reduced-motion: reduce) {
      .mode-btn, .led { transition: none; }
    }
  </style>
</head>
<body>
  <div class="unit">
    <div class="readout">Audio Hub &middot; MOTU M4</div>
    <h1>Source select</h1>
    <div class="modes">
      <button class="mode-btn" id="btn-spotify" onclick="setMode('spotify')">
        <span class="led"></span>Spotify
      </button>
      <button class="mode-btn" id="btn-supercollider" onclick="setMode('supercollider')">
        <span class="led"></span>SuperCollider
      </button>
      <button class="mode-btn" id="btn-mirror" onclick="setMode('mirror')">
        <span class="led"></span>Mirror laptop
      </button>
      <button class="mode-btn" id="btn-off" onclick="setMode('off')">
        <span class="led"></span>Off
      </button>
    </div>
    <div class="status-line">Active: <strong id="status-text">checking&hellip;</strong></div>
  </div>
  <script>
    async function refresh() {
      const r = await fetch('/api/status');
      const data = await r.json();
      document.getElementById('status-text').textContent = data.mode;
      ['spotify', 'supercollider', 'mirror', 'off'].forEach(m => {
        document.getElementById('btn-' + m).classList.toggle('active', data.mode === m);
      });
    }
    async function setMode(mode) {
      document.getElementById('status-text').textContent = 'switching to ' + mode + '...';
      await fetch('/api/mode/' + mode, { method: 'POST' });
      refresh();
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>"""

@app.route("/")
def index():
    return INDEX_HTML

@app.route("/api/status")
def status():
    out = subprocess.run(["sudo", "-n", SCRIPT, "status"], capture_output=True, text=True)
    return jsonify(mode=out.stdout.strip() or "unknown")

@app.route("/api/mode/<mode>", methods=["POST"])
def set_mode(mode):
    if mode not in ("spotify", "supercollider", "mirror", "off"):
        return jsonify(error="invalid mode"), 400
    subprocess.run(["sudo", "-n", SCRIPT, mode], check=True)
    return jsonify(ok=True, mode=mode)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
