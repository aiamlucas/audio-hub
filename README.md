# Audio-Hub: An Integrated Headless Audio Server using a USB Audio Interface, Spotify Connect and a SuperCollider Server

A headless Debian box that turns any class-compliant USB audio interface into a shared network audio device:
- Plays audio out through your **audio interface** (connected to your speakers). This guide can be used with any interface, I used a **MOTU M4** as the running concrete example throughout, since that's what's actually being used to test every step here. Nearly all modern interfaces (Focusrite Scarlett, Behringer UMC, PreSonus, and the M4 among others) work identically on Linux, since Linux talks to all of them through the same generic driver rather than anything vendor-specific. 
- Acts as a **Spotify Connect** target you pick from the Spotify app on your phone
- Runs a **SuperCollider server (scsynth)** you can connect to from SuperCollider on your own laptop, over the network
- Optionally **mirrors your laptop's own audio output** straight to the speakers
- Since the interface can only be "held" by one source at a time, a tiny **web page with buttons** lets you switch between modes

## Architecture

Class-compliant USB audio interfaces are handled by Linux's generic `snd-usb-audio` driver, no vendor driver needed, for the M4 or any other interface in this category. Only one program can have exclusive control of the interface at a time, so we treat "Spotify," "SuperCollider," and "Mirror" as mutually-exclusive service stacks:

- **Spotify stack** = `raspotify` (a systemd-wrapped build of the open-source `librespot` Spotify Connect client), talking to the interface directly via ALSA.
- **SuperCollider stack** = `jackd` (JACK audio server, bound to the interface) + `scsynth` (SuperCollider's audio server, connected to JACK). On Linux, SuperCollider's server is built against JACK, not raw ALSA, so JACK is required in between.
- **Mirror stack** (optional, Step 10) = no dedicated service, your laptop pushes audio to `aplay` over an SSH connection.

A small shell script stops whichever stack is active and starts the requested one. A small Flask web app calls that script and shows the buttons. That's the whole system.

## Before you start, gather this info

- Your Wi-Fi network name (SSID) and password
- The username you're logged in as on the mini-PC (referred to below as `youruser`, replace it everywhere)
- Your audio interface, plugged into the mini-PC or the device you want to use as the Audio-Hub

## About this repo
 
Files specific to Audio-Hub itself are included here as real files, so you can copy them directly instead of retyping. The other config files touch things that may already exist on your system, so those steps have you check first and modify, append, or create as needed.
 
```
scripts/audio-mode.sh                → /usr/local/bin/audio-mode.sh                    (Step 7)
web/app.py                           → /opt/audio-hub/app.py                            (Step 8)
systemd/audio-control-panel.service  → /etc/systemd/system/audio-control-panel.service   (Step 8)
systemd/audio-hub-default.service    → /etc/systemd/system/audio-hub-default.service     (Step 9)
```
 
**Not shipped as files, check what's there first, in the step itself:** `jackd.service` and `scsynth.service` (Step 6) use generic enough names that an earlier, unrelated JACK/SuperCollider setup could already occupy them; the `getty@tty1.service.d` override (Step 9) sits on top of a core systemd unit that isn't ours to begin with; and `/etc/raspotify/conf` (Step 5) is guaranteed to already exist, fully populated, the moment the `raspotify` package installs. Each of those steps below starts with a check for what's already there and walks through editing it in place deliberately not something to blindly overwrite from a repo file.
 
All placeholders (`youruser`/`<mini-pc-ip>`/`YOUR_SUBNET`) apply the same way whether you're typing from the guide or copying a file.
 
Everything below is meant to be run **in order**. Steps 1–3 need to happen at the physical terminal (keyboard+monitor plugged into the mini-PC), since SSH isn't installed yet. 
From Step 3 onward you can do everything remotely over SSH if you prefer.
 
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
 
## Step 4 — Connect and verify your audio interface (MOTU M4 example)
 
Plug your interface into a USB port. If it's class-compliant — true of virtually every modern USB audio interface, M4 included — the kernel should pick it up with no extra driver:
 
```bash
dmesg | tail -30
cat /proc/asound/cards
```
 
You should see a line like:
 
```
4 [M4             ]: USB-Audio - M4
                      MOTU M4 at usb-0000:05:00.3-2, high speed
```
 
**This is the one step where "your device" and "the M4" genuinely diverge — pay attention to your own output here.** Whatever appears between the square brackets on the left (`M4` in this example) is your card's ALSA name, and it's what the rest of this guide means every time it says `M4` or `hw:M4`/`plughw:M4` — a Focusrite might show up as `USB` or `Scarlett2i2`, a Behringer as `UMC204HD`, and so on. From here on, substitute your own card's name wherever this guide writes `M4`.
 
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
 
We use `plughw` rather than `hw` here. The M4 doesn't support plain 16-bit playback (`speaker-test`'s default), so `hw:M4` fails with `Sample format not available for playback`. `plughw` adds a conversion layer that matches whatever format the app requests to whatever the hardware actually supports.
 
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
LIBRESPOT_ZEROCONF_PORT="42000"
```
 
Three corrections/additions versus what you might expect:
- `plughw:M4`, not `hw:M4` — same reason as Step 4's `speaker-test`: the M4 doesn't support the plain 16-bit format librespot defaults to, and `plughw` auto-converts.
- `LIBRESPOT_MIXER_TYPE`, not `LIBRESPOT_MIXER` — that's the actual option name (check `/etc/raspotify/conf`'s own comments if unsure). Environment variables that don't match a real option are silently ignored, so a typo here won't error, it'll just do nothing.
- `LIBRESPOT_ZEROCONF_PORT="42000"` — without this, `raspotify` picks a random TCP port each time it starts for the actual Spotify Connect pairing handshake (separate from the mDNS broadcast that just announces the device exists). Firewall rules can't target a port that changes every restart, so we pin it to a fixed number here and open exactly that port in Step 11.
After editing, double check it actually saved:
 
```bash
sudo grep -E "LIBRESPOT_DEVICE|LIBRESPOT_MIXER_TYPE|LIBRESPOT_ZEROCONF_PORT" /etc/raspotify/conf
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
 
Unlike the files in Step 7 and Step 8, `jackd.service` and `scsynth.service` are generic enough names that something could already be sitting at these paths — an earlier JACK tutorial you tried, or an old project. Check before creating either one:
 
```bash
ls -la /etc/systemd/system/jackd.service /etc/systemd/system/scsynth.service 2>/dev/null
```
 
**If that prints nothing for a file** — it doesn't exist, create it fresh: `sudo nvim /etc/systemd/system/jackd.service` and paste in the content below.
 
**If either already exists** — open it (`sudo nvim /etc/systemd/system/jackd.service`) and compare it against what's below rather than blindly overwriting. Worth a quick backup first either way: `sudo cp /etc/systemd/system/jackd.service /etc/systemd/system/jackd.service.bak`. The lines that actually matter for this setup to work are `User=`, `ExecStart=`, `LimitRTPRIO=`/`LimitMEMLOCK=`, and `Environment=JACK_NO_AUDIO_RESERVATION=1` — if an existing file is missing these or has different values, that's what needs to change.
 
> **Why `JACK_NO_AUDIO_RESERVATION=1`:** JACK normally checks with a D-Bus session bus before grabbing the audio device, to avoid conflicting with other apps (useful on a desktop). A `systemd` system service has no session bus at all, so that check just fails outright and JACK refuses to start (`Audio device ... cannot be acquired`). We don't need the check anyway — our switcher script (Step 7) already guarantees only one of `jackd`/`raspotify` runs at a time — so we disable it.
 
`/etc/systemd/system/jackd.service` should contain:
 
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
 
`/etc/systemd/system/scsynth.service` should contain:
 
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
 
Copy `scripts/audio-mode.sh` from this repo onto the mini-PC as `/usr/local/bin/audio-mode.sh` (`scp`, `git clone` directly on the mini-PC, or paste its contents into `sudo nvim /usr/local/bin/audio-mode.sh` — whichever's easiest). Briefly, what it does: takes one of `spotify`/`supercollider`/`mirror`/`off`/`status` as its argument, stops whichever stack is currently running, starts the requested one, and records the choice in a small state file so `status` can report it later.
 
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
 
Copy `web/app.py` from this repo onto the mini-PC as `/opt/audio-hub/app.py`. Briefly, what it does: serves the mode-switcher page at `/`, exposes `/api/status` and `/api/mode/<mode>` for the page's JavaScript to call, and shells out to `audio-mode.sh` via `sudo -n` for every actual state change — the Flask app itself never touches `systemctl` directly.
 
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
 
Right now, if you reboot, two things still need a human: the local console asks for the `youruser` login password (only matters if you ever plug in a keyboard/monitor), and none of `raspotify`/`jackd`/`scsynth` auto-start — you'd have to visit the web panel and tap a button before any sound works. Let's fix both, since you won't have a keyboard on this machine day to day.
 
### Skip the console login password
 
This only matters for the rare case you *do* plug in a keyboard/monitor to debug something. It has no effect on SSH — SSH already requires your key, not a password, since Step 3.
 
Unlike a fresh custom filename, `getty@tty1.service` itself is a core systemd unit that already exists and is active on every systemd-based install — we're not creating that, only adding an override to it. The override directory is uncommonly pre-populated on a stock Debian install, but check first rather than assume:
 
```bash
ls -la /etc/systemd/system/getty@tty1.service.d/ 2>/dev/null || echo "doesn't exist yet — safe to create"
```
 
**If it says "doesn't exist yet"** — proceed as below. **If it lists something** — open whatever's in there before adding anything; it may be doing something unrelated (a serial console setup, some other customization) that's worth understanding before you touch it.
 
```bash
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo nano /etc/systemd/system/getty@tty1.service.d/autologin.conf
```
 
Contents:
 
```ini
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin youruser --noclear %I $TERM
```
 
Apply it:
 
```bash
sudo systemctl daemon-reload
sudo systemctl restart getty@tty1.service
```
 
**Trade-off to know about:** this means anyone who plugs a keyboard and monitor into the machine gets a shell as `youruser` with zero password. Since the machine normally has nothing attached and this is purely a recovery fallback for you, that's a reasonable trade for convenience — just keep it in mind if the machine's physical location ever becomes less trusted (e.g. shared housing, public space).
 
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
 
Nothing to install. You already added `mirror` support to `/usr/local/bin/audio-mode.sh` and its sudoers rule back in Step 7 — if you followed the guide in order, there's nothing more to set up here. Test it:
 
```bash
sudo -k
sudo -n /usr/local/bin/audio-mode.sh mirror
sudo -n /usr/local/bin/audio-mode.sh status
```
 
Should print `mirror`. At this point the M4 is simply free and idle — that's the entire "mirror mode" state on the mini-PC side.
 
### On your laptop: send audio
 
Whenever you want to mirror, run this on your **laptop** (works on any Linux desktop using PulseAudio or PipeWire — which is virtually all of them by default):
 
```bash
parec --channels=2 --rate=48000 --format=s16le --device=@DEFAULT_SINK@.monitor --latency-msec=50 | ssh youruser@<mini-pc-ip> 'aplay -D plughw:M4 -f S16_LE -r 48000 -c 2 -B 100000'
```
 
- `parec` (not `parecord`) — this matters: PulseAudio's tools come in two flavors depending on name. `parecord`/`paplay` read and write *encoded* files (WAV, with a header) and will fail or produce garbage when piped as raw data. `parec`/`pacat` handle headerless raw PCM instead — the right choice for piping straight into another program like `aplay`.
- `@DEFAULT_SINK@.monitor` specifically means "whatever's currently coming out of my default output device" — your system audio, any app, not your microphone. If you hear silence on the mini-PC end, first confirm your laptop is actually routing audio through its *default* sink right now (`pactl info | grep "Default Sink"`) — it's easy to be listening through a device that isn't currently the default (e.g. headphones vs. built-in speakers), in which case the monitor captures silence even while you can hear something locally.
- `--latency-msec=50` on `parec` matters more than it looks — by default, `parec` requests a fairly generous buffer from PulseAudio/PipeWire, tuned for gapless reliability over responsiveness, which turns out to be the single biggest source of lag in this whole pipeline (far more than SSH or `aplay`'s own buffer). This flag asks for a much smaller one instead.
- `-B 100000` on the `aplay` side sets a 100ms buffer — network delivery over SSH isn't perfectly steady the way local playback is, and without some cushion here you'll get audible dropouts (`underrun!!!` in its output). 100ms is enough headroom to avoid that while staying reasonably tight; if you still hear glitches on a flakier network, raising this toward `300000`–`500000` trades some latency for more reliability.
- The part in quotes runs directly on the mini-PC over the SSH connection — no separate service needed there at all, since SSH itself carries the audio bytes to `aplay`'s stdin for as long as the connection stays open.
- `Ctrl+C` stops sending and closes the SSH connection; the mini-PC goes back to idle, still in Mirror mode, ready for next time.
Even tuned like this, expect somewhere around a couple hundred milliseconds of delay, not true real-time — fine for background music, not tight enough for comfortably watching synced video.
 
For convenience, turn it into a one-word command — add to your laptop's `~/.bashrc`:
 
```bash
alias audio-hub='parec --channels=2 --rate=48000 --format=s16le --device=@DEFAULT_SINK@.monitor --latency-msec=50 | ssh youruser@<mini-pc-ip> "aplay -D plughw:M4 -f S16_LE -r 48000 -c 2 -B 100000"'
```
 
Then it's: click "Mirror Laptop" on the web page once, type `audio-hub` whenever you actually want sound flowing, `Ctrl+C` when you're done.
 
**Worth knowing:** it plays through your laptop's own speakers *and* the M4 at the same time — `parec` only ever taps a copy of the signal from the monitor, it doesn't reroute or silence local playback. Volume comes from your laptop's normal volume control (or a tool like `pulsemixer`'s **Recording** tab, which lists `parec` directly, if you want to adjust it independently of everything else on the laptop).
 
This mode carries a real amount of latency — expect roughly a couple hundred milliseconds even tuned — since it's a general-purpose pipe (SSH-carried raw audio) rather than anything purpose-built for real-time streaming. Consider it experimental: fine for background music, not something to rely on for anything timing-sensitive.
 
---
 
## Step 11 — Lock everything to your local network (do this last)
 
Find your LAN subnet:
 
```bash
ip -4 addr show
```
 
Look for the `inet` line on your active interface (`wlp3s0`, `eth0`, etc.) — it'll look like `inet 192.168.X.Y/24`. Your subnet is that same address with the last number replaced by `0` — e.g. address `192.168.X.42/24` → subnet `192.168.X.0/24`.
 
> **Copy-paste trap:** the commands below use `YOUR_SUBNET` as a placeholder. Before running them, replace every instance with your actual subnet (e.g. `192.168.X.0/24`) — not a guess, not an example from this guide. A wrong subnet here doesn't error, it just silently firewalls out every device on your real network, including the one you're SSH'd in from.
 
```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from YOUR_SUBNET to any port 22 proto tcp
sudo ufw allow from YOUR_SUBNET to any port 8080 proto tcp
sudo ufw allow from YOUR_SUBNET to any port 57110 proto udp
sudo ufw allow from YOUR_SUBNET to any port 5353 proto udp
sudo ufw allow from YOUR_SUBNET to any port 42000 proto tcp
```
 
Two of these deserve a callout, since they're easy to miss and the device will *look* broken in a confusing way without them (everything else working, but Spotify just never showing up on your phone):
- `5353/udp` — mDNS (multicast DNS), the protocol your phone uses to *discover* "Mini-PC Speakers" exists at all.
- `42000/tcp` — matches `LIBRESPOT_ZEROCONF_PORT` from Step 5. mDNS only announces the device; your phone then makes a separate, ordinary TCP connection to this specific port to actually complete pairing. Without this rule, the phone can see the announcement but the follow-up connection silently fails, and Spotify just reports no devices found — which looks identical to the mDNS rule being missing, even though the actual cause is one layer deeper.
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
 

## Command Reference
 
| Thing                                         | Command                                                                                                                                                                                |
|-----------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Switch to Spotify                             | `sudo -n /usr/local/bin/audio-mode.sh spotify`                                                                                                                                         |
| Switch to SuperCollider                       | `sudo -n /usr/local/bin/audio-mode.sh supercollider`                                                                                                                                   |
| Switch to Mirror                              | `sudo -n /usr/local/bin/audio-mode.sh mirror`                                                                                                                                          |
| Turn audio off                                | `sudo -n /usr/local/bin/audio-mode.sh off`                                                                                                                                             |
| Current mode                                  | `sudo -n /usr/local/bin/audio-mode.sh status`                                                                                                                                          |
| Web panel                                     | `http://<mini-pc-ip>:8080`                                                                                                                                                             |
| SuperCollider connect (from your laptop)      | `Server.remote(\miniPC, NetAddr("<mini-pc-ip>", 57110))`                                                                                                                               |
| Mirror your laptop's audio (from your laptop) | `parec --channels=2 --rate=48000 --format=s16le --device=@DEFAULT_SINK@.monitor --latency-msec=50 \| ssh youruser@<mini-pc-ip> "aplay -D plughw:M4 -f S16_LE -r 48000 -c 2 -B 100000"` |
| Check M4 is seen                              | `cat /proc/asound/cards`                                                                                                                                                               |
| Spotify logs                                  | `journalctl -u raspotify -f`                                                                                                                                                           |
| SuperCollider logs                            | `journalctl -u scsynth -f`                                                                                                                                                             |
| JACK logs                                     | `journalctl -u jackd -f`                                                                                                                                                               |
 
 
## Troubleshooting
 
- **No sound in either mode:** confirm `cat /proc/asound/cards` still shows the M4 (USB audio devices sometimes need a reboot after first plug-in), and that nothing else is holding the device — `fuser -v /dev/snd/*` will show what has it open.
- **`jackd` fails to start:** the device name may not be `hw:M4` — rerun `aplay -l` and adjust the `-dhw:M4` argument in `jackd.service` to match.
- **SuperCollider connects but no sound:** check `jack_lsp -c` — if `SuperCollider:out_1/2` aren't connected to `system:playback_1/2`, run the `jack_connect` commands from the `ExecStartPost` line manually to see the actual error.
- **Web buttons do nothing:** run `sudo -n /usr/local/bin/audio-mode.sh status` as `youruser` directly — if it prompts for a password, your `/etc/sudoers.d/audio-mode` line doesn't match exactly (it must match the command *and* arguments verbatim).
- **Spotify device doesn't show up on phone:** your phone must be on the same Wi-Fi network as the mini-PC, and `raspotify` must actually be running (`sudo -n /usr/local/bin/audio-mode.sh spotify`, then check `journalctl -u raspotify -f`).
