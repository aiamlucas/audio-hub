# Debian Audio Hub: MOTU M4 + Spotify Connect + SuperCollider

A headless Debian box that:
- Plays audio out through your **MOTU M4** (connected to your speakers)
- Acts as a **Spotify Connect** target you pick from the Spotify app on your phone
- Runs a **SuperCollider server (scsynth)** you can connect to from SuperCollider on your own laptop, over the network
- Optionally **mirrors your laptop's own audio output** straight to the speakers (Step 10) — not your phone, just whichever laptop you run the sender command from
- Since the M4 can only be "held" by one source at a time, a tiny **web page with buttons** lets you switch between modes
- Is reachable only over SSH and the web page, restricted to your local network

## Architecture, in one paragraph

The M4 is a class-compliant USB audio interface, so Linux's generic `snd-usb-audio` driver handles it — no vendor driver needed. Only one program can have exclusive control of it at a time, so we treat "Spotify," "SuperCollider," and "Mirror" as mutually-exclusive service stacks:

- **Spotify stack** = `raspotify` (a systemd-wrapped build of the open-source `librespot` Spotify Connect client), talking to the M4 directly via ALSA.
- **SuperCollider stack** = `jackd` (JACK audio server, bound to the M4) + `scsynth` (SuperCollider's audio server, connected to JACK). On Linux, SuperCollider's server is built against JACK, not raw ALSA, so JACK is required in between.
- **Mirror stack** (optional, Step 10) = no dedicated service — your laptop pushes audio to `aplay` over an SSH connection, on demand, reusing the same key-based access as everything else.

A small shell script stops whichever stack is active and starts the requested one. A small Flask web app calls that script and shows the buttons. That's the whole system.

## Before you start, gather this info

- Your Wi-Fi network name (SSID) and password
- The username you're logged in as on the mini-PC (referred to below as `youruser` — replace it everywhere)
- Your router's LAN subnet (you'll confirm the exact value in Step 11 — no need to know it yet)

Everything below is meant to be run **in order**. Steps 1–3 need to happen at the physical terminal (keyboard+monitor plugged into the mini-PC), since SSH isn't installed yet. From Step 3 onward you can do everything remotely over SSH if you prefer.

---

## Step 1 — Install `sudo` for your user

Fresh minimal Debian installs often don't have `sudo` installed, and your user isn't in the `sudo` group yet. Log in as **root** first (either `su -` from your user, or log in directly as root if that's the account you have on the console):

```bash
su -
```

Then, as root:

```bash
apt update
apt install -y sudo
usermod -aG sudo youruser
exit
```

Log out and back in as `youruser` (or run `su - youruser`) so the new group membership takes effect. Confirm it worked:

```bash
sudo whoami
# should print: root
```

---

## Step 2 — Connect to Wi-Fi

We'll use NetworkManager — it's the least fiddly option for a headless box and reconnects automatically on boot.

```bash
sudo apt update
sudo apt install -y network-manager
sudo systemctl enable --now NetworkManager
```

Find your Wi-Fi interface name:

```bash
nmcli device status
```

You're looking for a device of type `wifi` (commonly `wlan0`). Then:

```bash
nmcli device wifi rescan
nmcli device wifi list
sudo nmcli device wifi connect "YOUR_SSID" password "YOUR_WIFI_PASSWORD"
```

Verify:

```bash
nmcli connection show --active
ip a
hostname -I
```

Note the IP address printed by `hostname -I` — you'll need it for SSH and for connecting from SuperCollider later. It's worth giving the mini-PC a **DHCP reservation** in your router's admin page so this IP never changes.

> If your Debian install already has an entry for a wired or wireless interface in `/etc/network/interfaces`, check `cat /etc/network/interfaces` — a conflicting config there can fight with NetworkManager. If you see `wlan0` mentioned, comment that block out.

---

## Step 3 — Install SSH (local-only, key-based)

```bash
sudo apt install -y openssh-server
sudo systemctl enable --now ssh
```

From your **main computer** (not the mini-PC), generate a key pair if you don't already have one, and copy it over:

```bash
ssh-keygen -t ed25519 -C "audio-hub"
ssh-copy-id youruser@<mini-pc-ip>
```

Confirm key login works:

```bash
ssh youruser@<mini-pc-ip>
```

Once that works, harden `/etc/ssh/sshd_config` on the mini-PC (`sudo nano /etc/ssh/sshd_config`):

```
PermitRootLogin no
PasswordAuthentication no
```

Restart SSH:

```bash
sudo systemctl restart ssh
```

We'll additionally restrict SSH to your LAN subnet with a firewall in Step 11 — that's what makes it "local only" rather than just "not exposed on purpose."

From here on, you can do everything over SSH from your main computer.

---

## Step 4 — Connect and verify the MOTU M4

Plug the M4 into a USB port. It's class-compliant, so the kernel should pick it up with no extra driver:

```bash
dmesg | tail -30
cat /proc/asound/cards
```

You should see a line like:

```
4 [M4             ]: USB-Audio - M4
                      MOTU M4 at usb-0000:05:00.3-2, high speed
```

Note the card's name (usually just `M4`) — we'll reference it as `hw:M4` from now on.

> If `dmesg` shows the M4 disconnecting/reconnecting several times before settling, or a `device descriptor read/64, error -71`, that's a flaky USB connection, not an audio problem. Try a USB port wired straight to the motherboard (not a hub or front-panel header) and/or a different cable if you see dropouts later.

`aplay` and `speaker-test` aren't part of a minimal Debian install — install them first:

```bash
sudo apt update
sudo apt install -y alsa-utils
```

List playback devices to confirm the M4 shows up:

```bash
aplay -l
```

Test it makes sound:

```bash
speaker-test -c 4 -D plughw:M4 -t wav
# Ctrl+C to stop after you hear tones on your speakers
```

We use `plughw` rather than `hw` here — the M4 doesn't support plain 16-bit playback (`speaker-test`'s default), so `hw:M4` fails with `Sample format not available for playback`. `plughw` adds a conversion layer that matches whatever format the app requests to whatever the hardware actually supports.

If you hear nothing, check the M4 isn't muted/gain-zeroed at the hardware knobs, and try `alsamixer -c M4` to check for any software-side mute.

> Keep this format quirk in mind later: `jackd` (Step 6) and `raspotify` (Step 5) both talk to `hw:M4` directly rather than through `plughw`. If either fails to start with a similar "sample format"/"invalid argument" error, the fix is the same idea — either point them at `plughw:M4` instead, or pin them to the M4's native format explicitly (commonly `S24_3LE` or `S32_LE` — we'll adjust if you hit this).

---

## Step 5 — Spotify Connect (raspotify / librespot)

[Raspotify](https://github.com/dtcooper/raspotify) is a systemd-packaged build of `librespot`, the open-source Spotify Connect client you were thinking of. Despite the name it works on any Debian Stable system, not just Raspberry Pi.

```bash
sudo apt-get -y install curl
curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
```

This adds an apt repo, installs the package, and creates a `raspotify.service` that starts automatically. We're going to manage it ourselves through the switcher instead (so it doesn't fight SuperCollider for the M4 on boot):

```bash
sudo systemctl stop raspotify
sudo systemctl disable raspotify
```

Configure it — edit `/etc/raspotify/conf`:

```bash
sudo nano /etc/raspotify/conf
```

Set (uncomment/add) these:

```
LIBRESPOT_NAME="Mini-PC Speakers"
LIBRESPOT_BACKEND="alsa"
LIBRESPOT_DEVICE="plughw:M4"
LIBRESPOT_MIXER_TYPE="softvol"
LIBRESPOT_BITRATE="320"
LIBRESPOT_INITIAL_VOLUME="70"
```

Two corrections versus what you might expect:
- `plughw:M4`, not `hw:M4` — same reason as Step 4's `speaker-test`: the M4 doesn't support the plain 16-bit format librespot defaults to, and `plughw` auto-converts.
- `LIBRESPOT_MIXER_TYPE`, not `LIBRESPOT_MIXER` — that's the actual option name (check `/etc/raspotify/conf`'s own comments if unsure). Environment variables that don't match a real option are silently ignored, so a typo here won't error, it'll just do nothing.

After editing, double check it actually saved:

```bash
sudo grep -E "LIBRESPOT_DEVICE|LIBRESPOT_MIXER_TYPE" /etc/raspotify/conf
```

(Note the `sudo` — `/etc/raspotify/conf` isn't world-readable, so a plain `grep` without `sudo` will fail with "Permission denied.")

Test it manually:

```bash
sudo systemctl start raspotify
sudo systemctl status raspotify
```

Open Spotify on your phone (same Wi-Fi network), tap the "Connect to a device" icon, and you should see **"Mini-PC Speakers"** appear. Select it and play something. When you're done testing:

```bash
sudo systemctl stop raspotify
```

(Requires a Spotify Premium account — librespot only supports Connect for Premium.)

---

## Step 6 — SuperCollider server (JACK + scsynth)

On Linux, SuperCollider's server (`scsynth`) is built against **JACK**, not raw ALSA — so JACK sits between scsynth and the M4.

```bash
sudo apt install -y jackd2 supercollider-server
```

During `jackd2`'s install you'll likely be asked *"Enable realtime process priority for jackd?"* — say **Yes**. Make sure your user is in the `audio` group (this is usually done automatically, but confirm):

```bash
groups youruser
sudo usermod -aG audio youruser
```

Being in the `audio` group isn't quite enough on its own — the kernel also needs explicit permission to give JACK realtime scheduling priority and to lock memory (so audio buffers can't get swapped to disk mid-playback). Check whether that's already set up:

```bash
cat /etc/security/limits.d/audio.conf 2>/dev/null || echo "not found"
```

If it's missing, create it:

```bash
sudo tee /etc/security/limits.d/audio.conf > /dev/null <<'EOF'
@audio   -  rtprio     95
@audio   -  memlock    unlimited
EOF
```

Log all the way out and back in (a fresh SSH session — these limits only apply at login time, not to a shell you already have open) and confirm they took effect:

```bash
ulimit -r   # should show 95, not 0
ulimit -l   # should show "unlimited"
```

**If those still show `0` and a small number** (not `95`/`unlimited`) even after a fresh login, even though `id` confirms you're in the `audio` group and `audio.conf` is correct — you've hit a separate, known issue: modern `systemd` sets its own default resource-limit ceilings (`DefaultLimitRTPRIO`/`DefaultLimitMEMLOCK`, default `0` on recent versions) for every session it manages, including SSH logins, and these can silently override what PAM's `limits.d` grants. Fix it by raising systemd's own ceiling:

```bash
sudo mkdir -p /etc/systemd/system.conf.d
sudo tee /etc/systemd/system.conf.d/audio-limits.conf > /dev/null <<'EOF'
[Manager]
DefaultLimitRTPRIO=95
DefaultLimitMEMLOCK=infinity
EOF
sudo reboot
```

Reconnect after ~30 seconds and recheck `ulimit -r` / `ulimit -l` — they should be correct now.

Now test JACK by hand:

```bash
jackd -P70 -dalsa -dhw:M4 -r48000 -p256 -n2
```

Watch for errors about opening the device (wrong name) vs. normal startup chatter — you're specifically watching for `Cannot use real-time scheduling` or `Cannot lock down memory area`, which mean the limits above didn't take effect. Ctrl+C to stop once you've confirmed it starts cleanly.

### Systemd services

> **Note:** the PAM limits you just set up only apply to *login sessions* — a `systemd` service doesn't go through that path at all. So the unit file below sets the same limits directly via `LimitRTPRIO=`/`LimitMEMLOCK=`, independent of the PAM config. Both are worth having: PAM limits for when you run `jackd` by hand to test, and the systemd directives for the actual running service.

Create `/etc/systemd/system/jackd.service`:

> **Why `JACK_NO_AUDIO_RESERVATION=1`:** JACK normally checks with a D-Bus session bus before grabbing the audio device, to avoid conflicting with other apps (useful on a desktop). A `systemd` system service has no session bus at all, so that check just fails outright and JACK refuses to start (`Audio device ... cannot be acquired`). We don't need the check anyway — our switcher script (Step 7) already guarantees only one of `jackd`/`raspotify` runs at a time — so we disable it.

```ini
[Unit]
Description=JACK Audio Server (MOTU M4)
After=sound.target

[Service]
Type=simple
User=youruser
Group=audio
LimitRTPRIO=95
LimitMEMLOCK=infinity
Environment=JACK_NO_AUDIO_RESERVATION=1
ExecStart=/usr/bin/jackd -P70 -dalsa -dhw:M4 -r48000 -p256 -n2
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/scsynth.service`:

```ini
[Unit]
Description=SuperCollider audio server
After=jackd.service
Requires=jackd.service
BindsTo=jackd.service

[Service]
Type=simple
User=youruser
Group=audio
Environment=SC_JACK_DEFAULT_OUTPUTS=system:playback_1,system:playback_2
ExecStart=/usr/bin/scsynth -u 57110 -B 0.0.0.0 -a 1024 -i 0 -o 2 -l 32
ExecStartPost=/bin/sh -c 'sleep 2; jack_connect SuperCollider:out_1 system:playback_1 || true; jack_connect SuperCollider:out_2 system:playback_2 || true'
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Notes on those flags:
- `-u 57110` — the OSC/UDP port your SuperCollider client will talk to.
- `-B 0.0.0.0` — **required**. Since SuperCollider 3.10.3, `scsynth` only listens on `127.0.0.1` by default (a security fix), so without this flag your laptop's SuperCollider could never reach it. We lock this down to LAN-only with the firewall in Step 11 instead.
- `-i 0 -o 2` — no audio input, stereo output. Raise `-i` to 2 or 4 if you later want SuperCollider to process input from the M4's mic/line inputs (and add matching `jack_connect` lines for `system:capture_1`, etc.).
- `-l 32` — max simultaneous client logins. `scsynth` defaults to 64, but `sclang` (your SuperCollider client) hard-caps its own side at 32 and throws a `maxLogins should be <= 32` error on connect if the server reports anything higher — capping it here avoids that error, and 32 is far more than you'll ever need for one client.
- The `ExecStartPost` line auto-connects scsynth's stereo output to the M4's physical outputs via JACK.

Reload systemd and test manually:

```bash
sudo systemctl daemon-reload
sudo systemctl start jackd scsynth
sudo systemctl status jackd scsynth
jack_lsp -c
```

`jack_lsp -c` should show `SuperCollider:out_1`/`out_2` connected to `system:playback_1`/`playback_2`.

Also worth confirming the process that's actually running matches what you put in the unit file — it's easy for an edit to not fully save, or an old process to still be running from before your last change:

```bash
ps -C scsynth -o pid,user,args
```

The `args` column should show every flag you expect, e.g. `-l 32` — if it doesn't match `scsynth.service`, that mismatch (not the flags file itself) is the actual bug to chase; `sudo systemctl daemon-reload && sudo systemctl restart scsynth` and re-check.

Stop when done testing:

```bash
sudo systemctl stop scsynth jackd
```

Don't `enable` either of these — the switcher (Step 7) starts/stops them on demand.

### Connecting from SuperCollider on your own computer

In your local SuperCollider IDE:

```supercollider
s = Server.remote(\miniPC, NetAddr("<mini-pc-ip>", 57110));
// wait a moment for it to register, then:
{SinOsc.ar(440, 0, 0.1)!2}.play(s);
```

`Server.remote` tells sclang "there's already a server running over there," rather than trying to launch a new local one. You'll need the firewall (Step 11) to allow UDP 57110 from your computer's IP.

---

## Step 7 — The switcher script

This is the piece that enforces "only one at a time" and gives the web page something to call.

Create `/usr/local/bin/audio-mode.sh`:

```bash
#!/bin/bash
set -e
STATE_DIR=/run/audio-hub
STATE_FILE="$STATE_DIR/mode"
mkdir -p "$STATE_DIR"

case "$1" in
  spotify)
    systemctl stop scsynth jackd 2>/dev/null || true
    systemctl start raspotify
    echo spotify > "$STATE_FILE"
    ;;
  supercollider)
    systemctl stop raspotify 2>/dev/null || true
    systemctl start jackd
    systemctl start scsynth
    echo supercollider > "$STATE_FILE"
    ;;
  mirror)
    systemctl stop raspotify scsynth jackd 2>/dev/null || true
    echo mirror > "$STATE_FILE"
    ;;
  off)
    systemctl stop raspotify scsynth jackd 2>/dev/null || true
    echo off > "$STATE_FILE"
    ;;
  status)
    if systemctl is-active --quiet raspotify; then echo spotify;
    elif systemctl is-active --quiet scsynth; then echo supercollider;
    elif [ -f "$STATE_FILE" ]; then cat "$STATE_FILE";
    else echo off; fi
    ;;
  *)
    echo "Usage: $0 {spotify|supercollider|mirror|off|status}" >&2
    exit 1
    ;;
esac
```

> **Why `mirror` looks different from the other modes:** unlike Spotify and SuperCollider, Mirror (Step 10) has no service of its own to start — it just needs the M4 free, since your laptop pushes audio to it on demand over SSH rather than something on the mini-PC waiting for a connection. `status` can't ask a running service whether Mirror is active (there isn't one), so a small state file in `/run/audio-hub` — cleared on every reboot, which is fine — remembers the last mode you picked whenever no service is actively running.

```bash
sudo chown root:root /usr/local/bin/audio-mode.sh
sudo chmod 755 /usr/local/bin/audio-mode.sh
```

Let your user run *exactly this script, with exactly these arguments* as root, without a password — nothing broader:

```bash
sudo visudo -f /etc/sudoers.d/audio-mode
```

Contents (replace `youruser` with your actual username — same as everywhere else in this guide):

```
youruser ALL=(root) NOPASSWD: /usr/local/bin/audio-mode.sh spotify, /usr/local/bin/audio-mode.sh supercollider, /usr/local/bin/audio-mode.sh mirror, /usr/local/bin/audio-mode.sh off, /usr/local/bin/audio-mode.sh status
```

Editing through `visudo -f` (rather than a plain editor) matters here — it sets the file's permissions to exactly `0440` automatically, which sudo requires for any file in `sudoers.d`; anything else and sudo silently ignores the whole file rather than risk running with a tampered config. If you ever edit this file again with something other than `visudo -f`, re-lock the permissions afterward:

```bash
sudo chown root:root /etc/sudoers.d/audio-mode
sudo chmod 0440 /etc/sudoers.d/audio-mode
```

Confirm sudo actually accepted the file:

```bash
sudo visudo -c
```

Should report `parsed OK` for everything, with no permissions warning.

**Test with a clean slate** — `sudo -k` first clears any recently-cached "you typed your password" credential from your current session, so the test below reflects what this *specific rule* grants, not leftover trust from unrelated `sudo` commands you've run in the same session:

```bash
sudo -k
sudo -n /usr/local/bin/audio-mode.sh status
sudo -n /usr/local/bin/audio-mode.sh spotify
sudo -n /usr/local/bin/audio-mode.sh status
sudo -n /usr/local/bin/audio-mode.sh mirror
sudo -n /usr/local/bin/audio-mode.sh status
sudo -n /usr/local/bin/audio-mode.sh off
```

None of these should ever prompt for a password.

---

## Step 8 — The web control panel

```bash
sudo apt install -y python3-flask
sudo mkdir -p /opt/audio-hub
```

Create `/opt/audio-hub/app.py` (e.g. `sudo nvim /opt/audio-hub/app.py`) with this content:

```python
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
```

Design notes: the LED dots are genuine status indicators (only the active mode's LED lights up, amber, with a soft glow), not decorative icons — everything else is typography and structure. `Roboto`/`Roboto Mono` load from Google Fonts over the internet (fine for a phone or laptop browser, which have their own connection) with a system-font fallback if that's ever unreachable.

Systemd service, `/etc/systemd/system/audio-control-panel.service`:

```ini
[Unit]
Description=Audio Hub control panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/opt/audio-hub
ExecStart=/usr/bin/python3 /opt/audio-hub/app.py
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now audio-control-panel
```

Visit `http://<mini-pc-ip>:8080` from your phone or laptop — you'll get the mode buttons. This is the switcher UI you asked for (the Mirror button won't do anything useful until Step 10 sets up its receiver).

---

## Step 9 — Boot straight into a working system (no keyboard needed)

Right now, if you reboot, two things still need a human: the local console asks for the `username` login password (only matters if you ever plug in a keyboard/monitor), and none of `raspotify`/`jackd`/`scsynth` auto-start — you'd have to visit the web panel and tap a button before any sound works. Let's fix both, since you won't have a keyboard on this machine day to day.

### Skip the console login password

This only matters for the rare case you *do* plug in a keyboard/monitor to debug something. It has no effect on SSH — SSH already requires your key, not a password, since Step 3.

```bash
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo nano /etc/systemd/system/getty@tty1.service.d/autologin.conf
```

Contents:

```ini
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin username --noclear %I $TERM
```

Apply it:

```bash
sudo systemctl daemon-reload
sudo systemctl restart getty@tty1.service
```

**Trade-off to know about:** this means anyone who plugs a keyboard and monitor into the machine gets a shell as `username` with zero password. Since the machine normally has nothing attached and this is purely a recovery fallback for you, that's a reasonable trade for convenience — just keep it in mind if the machine's physical location ever becomes less trusted (e.g. shared housing, public space).

### Make sound work immediately on boot, with no visit to the web panel

`raspotify`, `jackd`, and `scsynth` are deliberately left disabled (Step 7) so the switcher fully controls them. That means after a reboot, nothing plays until something calls `audio-mode.sh`. Let's have boot itself call it, defaulting to Spotify since that's your primary use case (phone control):

Create `/etc/systemd/system/audio-hub-default.service`:

```ini
[Unit]
Description=Start default audio mode at boot
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/audio-mode.sh spotify

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable audio-hub-default.service
```

Now on every boot, with zero input from you: Wi-Fi reconnects (automatic since Step 2), SSH comes up, the web panel comes up (enabled in Step 8), and "Mini-PC Speakers" becomes selectable on your phone immediately.

You can still switch to SuperCollider or off anytime from the web panel — this only decides what's active right after power-on. To default to SuperCollider or nothing instead, change `spotify` in the `ExecStart` line to `supercollider` or `off`.

Test it:

```bash
sudo reboot
```

Give it 30–60 seconds, then check from your phone — the device should already be selectable with no SSH or web panel visit needed.

---

## Step 10 — Mirror your laptop's audio (optional)

A fourth mode: your laptop's current audio output streams to the M4, on demand, from your laptop only — nothing to do with your phone or Spotify Connect at all, a completely separate pathway. Like Spotify and SuperCollider, it's exclusive with them.

This reuses the SSH key access you already set up in Step 3, rather than opening any new port. There's no dedicated receiver service to run — clicking "Mirror" on the web page just stops Spotify/SuperCollider and frees the M4; your laptop then pushes audio straight to it, on demand, over an authenticated SSH connection. Nothing sits listening on the network waiting for a connection the way `raspotify` or `scsynth` do.

### On the mini-PC

Nothing to install — `aplay` (Step 4) and `ssh` (Step 3) are already in place. You already added `mirror` support to `/usr/local/bin/audio-mode.sh` and its sudoers rule back in Step 7 — if you followed the guide in order, there's nothing more to set up here. Test it:

```bash
sudo -k
sudo -n /usr/local/bin/audio-mode.sh mirror
sudo -n /usr/local/bin/audio-mode.sh status
```

Should print `mirror`. At this point the M4 is simply free and idle — that's the entire "mirror mode" state on the mini-PC side.

### On your laptop: send audio

Whenever you want to mirror, run this on your **laptop** (works on any Linux desktop using PulseAudio or PipeWire — which is virtually all of them by default):

```bash
parecord --channels=2 --rate=48000 --format=s16le --device=@DEFAULT_SINK@.monitor | ssh youruser@<mini-pc-ip> 'aplay -D plughw:M4 -f S16_LE -r 48000 -c 2'
```

- `parecord` comes from the `libpulse` package on Arch (`pulseaudio-utils` on Debian/Ubuntu, `pulseaudio-utils` on Fedora) — check with `which parecord` first; it's very likely already installed as part of your desktop audio stack, since it works fine against PipeWire's PulseAudio-compatible layer too.
- `@DEFAULT_SINK@.monitor` specifically means "whatever's currently coming out of my default output device" — your system audio, any app, not your microphone.
- The part in quotes runs directly on the mini-PC over the SSH connection — no separate service needed there at all, since SSH itself carries the audio bytes to `aplay`'s stdin for as long as the connection stays open.
- `Ctrl+C` stops sending and closes the SSH connection; the mini-PC goes back to idle, still in Mirror mode, ready for next time.

For convenience, turn it into a one-word command — add to your laptop's `~/.zshrc` or `~/.bashrc`:

```bash
alias mirror-audio='parecord --channels=2 --rate=48000 --format=s16le --device=@DEFAULT_SINK@.monitor | ssh youruser@<mini-pc-ip> "aplay -D plughw:M4 -f S16_LE -r 48000 -c 2"'
```

Then it's: click "Mirror Laptop" on the web page once, type `mirror-audio` whenever you actually want sound flowing, `Ctrl+C` when you're done.

**Test it:** click "Mirror Laptop" on the web page, run `mirror-audio` (or the full command) on your laptop, and play anything — a browser tab, a local file, whatever. It should come out of the speakers on the M4.

---

## Step 11 — Lock everything to your local network (do this last)

Find your LAN subnet:

```bash
ip -4 addr show
```

Look for the `inet` line on your active interface (`wlp3s0`, `eth0`, etc.) — it'll look like `inet 192.168.X.Y/24`. Your subnet is that same address with the last number replaced by `0` — e.g. address `192.168.0.xxx/24` → subnet `192.168.0.0/24`.

> **Copy-paste trap:** the commands below use `YOUR_SUBNET` as a placeholder. Before running them, replace every instance with your actual subnet (e.g. `192.168.0.0/24`) — not a guess, not an example from this guide. A wrong subnet here doesn't error, it just silently firewalls out every device on your real network, including the one you're SSH'd in from.

```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from YOUR_SUBNET to any port 22 proto tcp
sudo ufw allow from YOUR_SUBNET to any port 8080 proto tcp
sudo ufw allow from YOUR_SUBNET to any port 57110 proto udp
```

Double-check the SSH rule is in — and that it shows *your* subnet, not the placeholder — before enabling, so you don't lock yourself out over an active SSH session:

```bash
sudo ufw status verbose
sudo ufw enable
```

**Before closing this terminal**, open a second one and confirm a brand-new connection still works:

```bash
ssh youruser@<mini-pc-ip>
```

If it fails, go back to the still-open first session and run `sudo ufw disable` immediately, then re-check the subnet in your rules against the actual output of `ip -4 addr show`.

Nothing here is reachable from outside your LAN — no router port-forwarding is involved anywhere in this setup.

---

## Cheatsheet

| Thing | Command |
|---|---|
| Switch to Spotify | `sudo -n /usr/local/bin/audio-mode.sh spotify` |
| Switch to SuperCollider | `sudo -n /usr/local/bin/audio-mode.sh supercollider` |
| Switch to Mirror | `sudo -n /usr/local/bin/audio-mode.sh mirror` |
| Turn audio off | `sudo -n /usr/local/bin/audio-mode.sh off` |
| Current mode | `sudo -n /usr/local/bin/audio-mode.sh status` |
| Web panel | `http://<mini-pc-ip>:8080` |
| SuperCollider connect (from your laptop) | `Server.remote(\miniPC, NetAddr("<mini-pc-ip>", 57110))` |
| Mirror your laptop's audio (from your laptop) | `parecord --channels=2 --rate=48000 --format=s16le --device=@DEFAULT_SINK@.monitor \| ssh youruser@<mini-pc-ip> "aplay -D plughw:M4 -f S16_LE -r 48000 -c 2"` |
| Check M4 is seen | `cat /proc/asound/cards` |
| Spotify logs | `journalctl -u raspotify -f` |
| SuperCollider logs | `journalctl -u scsynth -f` |
| JACK logs | `journalctl -u jackd -f` |

## Troubleshooting

- **No sound in either mode:** confirm `cat /proc/asound/cards` still shows the M4 (USB audio devices sometimes need a reboot after first plug-in), and that nothing else is holding the device — `fuser -v /dev/snd/*` will show what has it open.
- **`jackd` fails to start:** the device name may not be `hw:M4` — rerun `aplay -l` and adjust the `-dhw:M4` argument in `jackd.service` to match.
- **SuperCollider connects but no sound:** check `jack_lsp -c` — if `SuperCollider:out_1/2` aren't connected to `system:playback_1/2`, run the `jack_connect` commands from the `ExecStartPost` line manually to see the actual error.
- **Web buttons do nothing:** run `sudo -n /usr/local/bin/audio-mode.sh status` as `youruser` directly — if it prompts for a password, your `/etc/sudoers.d/audio-mode` line doesn't match exactly (it must match the command *and* arguments verbatim).
- **Spotify device doesn't show up on phone:** your phone must be on the same Wi-Fi network as the mini-PC, and `raspotify` must actually be running (`sudo -n /usr/local/bin/audio-mode.sh spotify`, then check `journalctl -u raspotify -f`).
