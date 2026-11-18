# ODIN

An offline, voice-only assistant for this laptop. No cloud, no account, no
network at runtime: the wake word, the speech recognition and the voice all run
locally. Say **"hey odin"** or **"hi odin"**, wait for the beep, then give a
command.

Built for Ubuntu 26.04 on GNOME/Wayland, PipeWire audio, CPU only.

---

## Quick start

```bash
sudo scripts/setup-system.sh    # the only step that needs root
scripts/setup.sh                # virtualenv, packages, models  (needs internet once)
./odin.sh check                 # verify everything
scripts/install-service.sh      # run it always, from login
```

Then say: *"hey odin"* … *beep* … *"volume up"*.

## Everyday commands

```bash
./odin.sh check          # validate deps, models, phrases and apps
./odin.sh phrases        # list everything ODIN understands
./odin.sh mic-test       # live input level meter
./odin.sh wake-test      # show wake-word detections as they happen
./odin.sh listen-test    # recognise commands without acting on them
./odin.sh do "volume up" # run a command by text, no voice involved
./odin.sh say "hello"    # test the voice
./odin.sh devices        # list microphones
./odin.sh run            # run in the foreground (Ctrl-C to stop)
```

Service control:

```bash
systemctl --user status odin
journalctl --user -u odin -f      # watch what it hears
systemctl --user restart odin     # after editing config.toml
systemctl --user stop odin
```

## What it understands

| Area | Say |
|---|---|
| Media | play · pause · next track · previous track · stop · play music · pause music |
| Volume | volume up · volume down · louder · quieter · mute · sound on · max volume · set volume to sixty percent · half volume |
| Apps | open firefox / chrome / spotify / telegram / terminal / files / settings / calculator / code / media player / … (also "launch …" or "start …") |
| System | lock the screen · suspend · take a screenshot · brightness up · brightness down · what time is it · what day is it |
| Power | shut down · reboot  *(both ask "Are you sure?" and need a spoken "yes")* |
| ODIN | stop listening · start listening · are you there · never mind |

`./odin.sh phrases` prints the authoritative list. To add an app, add a line to
`[apps]` in `config.toml` and run `./odin.sh check` — it will tell you if the
words you chose can be recognised.

## How it works

```
mic ─► 16 kHz mono ─► Vosk + WAKE grammar ["hey odin","hi odin"]
                                │ match
                                ▼
                      beep ─► listen up to 6 s
                                │
                      Vosk + COMMAND grammar (the ~226 known phrases)
                                │ N-best hypotheses
                                ▼
                      intent match ─► action ─► beep / spoken reply
```

The whole design rests on one decision: **both recognisers use a closed
grammar**. Vosk is told the only phrases that are legal, so instead of
transcribing open-ended English on a laptop microphone it picks the best match
from a known set. That is what makes this accurate on a 2016 dual-core CPU with
no GPU and no training step — and why it works offline from the first run.

Two consequences worth knowing:

* **Only configured words can ever be heard.** A word that is not in the Vosk
  lexicon is silently dropped from the grammar, so `odin check` validates every
  phrase against the lexicon. This is why "sound on" is the unmute phrase:
  "unmute" is not in the lexicon.
* **A grammar is a bigram model, not a strict phrase list.** Short single words
  can lose out to common function words, so the listener takes the top N
  hypotheses and keeps the best one that is actually a command
  (`[listen] alternatives` in `config.toml`).

### Why these particular mechanisms

GNOME on Wayland blocks most of the obvious approaches, so each action uses the
route that actually works:

| Action | Route | Why not the obvious one |
|---|---|---|
| play / pause | MPRIS via `playerctl` | Wayland does not allow synthesising media keys |
| volume | `wpctl` (WirePlumber) | PipeWire owns the sink, not ALSA |
| open app | `gtk-launch` on the `.desktop` id | starts the app exactly as a click would |
| lock | `org.gnome.ScreenSaver` | `loginctl` needs a session the daemon does not have |
| screenshot | xdg-desktop-portal | GNOME denies `org.gnome.Shell.Screenshot` to external callers |
| brightness | sysfs + udev rule | GNOME exposes D-Bus brightness for the keyboard only, not the screen |

## Configuration

Everything lives in `config.toml`. The settings worth touching:

| Setting | Meaning |
|---|---|
| `wake.phrases` | what wakes ODIN |
| `wake.allow_bare_name` | also wake on a bare "odin" (off: it collides with "open") |
| `listen.energy_threshold` | RMS above which a block counts as speech — raise it in a noisy room |
| `listen.silence_end_s` | how much trailing silence ends a command |
| `listen.alternatives` | how many hypotheses to consider |
| `wake.settle_ms` | audio dropped after waking, to skip the wake-word tail |
| `speech.volume` | how loud ODIN is |
| `volume.step`, `brightness.step` | how big one "up" is |
| `apps` | spoken alias → `.desktop` id |

Run `systemctl --user restart odin` after editing.

## Tuning it to your voice and room

1. `./odin.sh mic-test` — speak normally. If your speech peaks below
   `energy_threshold`, lower it; if silence reads above it, raise it.
2. `./odin.sh wake-test` — say "hey odin" from where you normally sit, a dozen
   times. Every detection prints. Then stay quiet for a few minutes with music
   or a video playing and count false triggers.
3. `./odin.sh listen-test` — run through the commands you actually use. Any
   phrase that is consistently misheard can be reworded in `config.toml` (for
   apps) or `odin/intents.py` (for everything else).

Two knobs matter most: `energy_threshold` for your room, and
`wake.allow_bare_name` for how eager ODIN is.

## Tests

```bash
.venv/bin/python -I -m unittest discover -s tests      # fast, no mic needed
.venv/bin/python -I scripts/selftest.py                # recognition, no mic
.venv/bin/python -I scripts/airtest.py                 # through the speakers
```

`selftest.py` synthesises each phrase with Piper and feeds it through the real
wake engine and command grammar, so it checks that the grammar, the wake
matching and the intent table all agree.

`airtest.py` goes further: it plays the wake word and a command out of the
speakers and reports what the running daemon actually did, so it covers the
speaker, the room, the microphone and the timing between them. It refuses to
trigger anything destructive. That test is what caught the one timing bug
found in development: the wake word is detected on a *partial* result, part-way
through "hey odin", so the tail of the wake word was still arriving and landed
in the command recogniser — which then ended the command window before the
speaker had said anything. The fix is in two parts: `wake.settle_ms` discards
the tail, and the listener now treats an utterance that yields no usable
command as a false start and keeps listening instead of giving up.

Neither test says anything about how well ODIN hears *you* — that is what
`wake-test` is for.

## Limits, stated plainly

* **No window control.** "Close Firefox" or "switch to Chrome" are not offered:
  GNOME on Wayland deliberately blocks scripted window control. Launching apps
  works perfectly. The alternatives would be killing processes by name (risks
  unsaved work) or a custom GNOME extension (breaks on updates).
* **Bare "odin" is off by default** — one syllable, and it collides with
  "open". Use "hey odin" or "hi odin", or turn it on in `config.toml`.
* **Range is about 1–2 m** on the internal microphone in a quiet room. The
  biggest enemy of wake-word accuracy is music playing through the laptop's own
  speakers. ODIN mutes its own microphone while it speaks, but it cannot do
  that for your music.
* **Brightness needs the udev rule** from `scripts/setup-system.sh`. Without
  it, ODIN says so instead of failing silently.
* **One command per wake.** Say the wake word again for the next one.
* **Internet is needed once**, by `scripts/setup.sh`, for the Python packages
  and the two model files. Nothing after that touches the network.

## Privacy

Audio is processed in memory and never written to disk. Nothing is sent
anywhere — there is no network code in ODIN at all. `[log] transcripts = true`
records what ODIN *heard* in the systemd journal, which is local; set it to
`false` to turn even that off.

## Layout

```
odin/
  __main__.py      CLI
  daemon.py        wake → listen → act loop
  audio.py         capture, levels, mic muting
  wake.py          wake-word engine (swappable)
  listen.py        command capture and endpointing
  intents.py       every phrase ODIN knows  ← edit to add commands
  vocab.py         lexicon validation
  speech.py        Piper voice and beeps
  dbusutil.py      D-Bus (lock, screenshot portal)
  actions/         media, volume, apps, system, meta
config.toml        all settings
scripts/           setup, service install, self-test
models/            Vosk model + Piper voice (not in git)
```

### Swapping the wake-word engine

`odin/wake.py` defines a small `WakeEngine` interface (`process`, `reset`). The
shipped `VoskWakeEngine` needs no training. If false triggers become annoying,
a trained openWakeWord model for "hey odin" can be dropped in behind the same
interface — roughly 3% of one core instead of 15–20%, at the cost of a one-time
training session.
