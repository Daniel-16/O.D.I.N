# ODIN — Offline Voice Assistant

**Target machine:** Ubuntu 26.04, GNOME/Wayland, Intel i7-6600U (4 threads), 14 GB RAM, no GPU
**Hard requirements:** 100% offline at runtime · always-on wake word · voice-only input · controls the system

---

## 1. Readiness audit (verified on this laptop, not assumed)

### Confirmed working
| Thing | Status |
|---|---|
| Python 3.14.4 | `vosk`, `sounddevice`, `numpy`, `onnxruntime`, `openwakeword`, `piper-tts` **all install cleanly** (test-installed in a throwaway venv) |
| Audio server | PipeWire 1.6.2 + WirePlumber; `wpctl` present → volume control path confirmed |
| MPRIS | Live and responding. Spotify, 3× VLC, Chrome all on the bus; queried Spotify → returned `'Paused'` |
| Microphone | 1 capture device: `card 0: CX20724 Analog` (internal mic) |
| Screenshot | `org.freedesktop.portal.Screenshot` + `org.gnome.Shell.Screenshot` both present |
| Brightness | `/sys/class/backlight/intel_backlight` exists |
| Lock / suspend | `loginctl`, `systemctl` present |
| Build tools | gcc, cmake, make, git, curl, `uv` |
| Disk | 52 GB free (models need ~150 MB) |

### Gaps to close before writing code
| Missing | Fix | Why it matters |
|---|---|---|
| **PortAudio runtime** | `apt install libportaudio2` | `import sounddevice` currently **fails outright** — no mic input at all without it |
| `playerctl` | `apt install playerctl` | play/pause/next via MPRIS |
| `brightnessctl` | `apt install brightnessctl` | brightness commands (ships its own udev rules, no root needed) |
| Vosk model | download `vosk-model-small-en-us-0.15` (40 MB) | confirmed reachable (HTTP 200) |
| Piper voice | download one `en_US` ONNX voice (~63 MB) | ODIN's speech |

### ⚠️ Trap caught
Ubuntu's apt package named **`piper` is a gaming-mouse configuration GUI**, not the TTS engine. Installing it would be a dead end. The real TTS is **pip `piper-tts` 1.8.0**, which I verified installs on Python 3.14.

---

## 2. How it works

```
┌─ always-on daemon (systemd --user, Restart=always) ─────────────┐
│                                                                 │
│  mic ──► 16 kHz mono ring buffer ──► RMS energy gate            │
│                                          │ (silence = no decode)│
│                                          ▼                      │
│                         Vosk + WAKE grammar                     │
│                    ["hey odin","hi odin","odin"]                │
│                                          │ match                │
│                                          ▼                      │
│                        *beep*  ──► LISTENING (≤6 s)             │
│                                          │                      │
│                         Vosk + COMMAND grammar                  │
│                     (only the ~40 phrases we support)           │
│                                          ▼                      │
│                    intent match ──► action ──► beep / speech    │
└─────────────────────────────────────────────────────────────────┘
```

**The core design decision:** both the wake word and the commands run on *one* 40 MB Vosk model, restricted by a **closed grammar**. Vosk is told the only legal phrases up front, so instead of transcribing open-ended English (error-prone on a laptop mic) it picks the best match from ~40 known phrases. This is what makes accuracy good on a 2016 CPU with no GPU and no training step. `intents.py` is the single source of truth: the grammar and the dispatch table are both generated from it, so a phrase can never exist in one and not the other.

**No audio ever leaves the machine, and none is written to disk** unless you explicitly turn on debug recording.

---

## 3. Layout

```
ODIN/
├── odin/
│   ├── __main__.py      # daemon + CLI subcommands
│   ├── audio.py         # capture, ring buffer, energy gate
│   ├── wake.py          # WakeEngine interface + VoskWakeEngine
│   ├── listen.py        # command capture, silence endpointing
│   ├── intents.py       # phrases → intents (generates the grammars)
│   ├── state.py         # SLEEPING → LISTENING → ACTING machine
│   ├── speech.py        # Piper TTS + beeps, mic-mute during playback
│   └── actions/
│       ├── media.py     # playerctl / MPRIS
│       ├── volume.py    # wpctl
│       ├── apps.py      # .desktop index + gtk-launch + aliases
│       └── system.py    # lock, suspend, screenshot, brightness, time
├── models/              # vosk + piper (gitignored)
├── systemd/odin.service
├── scripts/setup.sh     # the only step that needs internet
├── config.yaml          # mic device, app aliases, sensitivity, toggles
└── tests/
```

## 4. Command set (v1)

- **Media:** play · pause · next/previous track · stop
- **Volume:** up · down · mute · unmute · set volume to N percent · max volume
- **Apps:** `open <name>` — aliases built from your installed `.desktop` files (firefox, chrome, spotify, vlc, telegram, terminal, files, settings…)
- **System:** lock screen · sleep · screenshot · brightness up/down · what time is it · what's the date · shut down / restart *(these two require a spoken "yes" confirmation)*
- **Meta:** "odin stop listening" (sleep the daemon) · "odin wake up" · "never mind"

## 5. Build order — each phase ends in something you can test out loud

| # | Phase | You verify by |
|---|---|---|
| 0 | Prereqs + model download (`setup.sh`) | script prints a green readiness table |
| 1 | Audio capture | `odin mic-test` → live input-level meter |
| 2 | Wake word | `odin wake-test` → prints every candidate + score; you say "hey odin" across the room |
| 3 | Command recognition | `odin listen-test` → say commands, see matched intent, no action fired |
| 4 | Action layer | `odin do "volume up"` → each action testable with zero voice involved |
| 5 | Speech + beeps | ODIN talks back; confirms it does not hear itself |
| 6 | systemd service | reboot → ODIN is already listening |
| 7 | Tuning pass | we adjust thresholds against your actual voice, mic, and room |

---

## 6. Risks, stated honestly up front

1. **Window control is out of scope.** "Close Firefox" / "switch to Chrome" cannot be done reliably — GNOME on Wayland deliberately blocks scripted window control. *Launching* apps works perfectly. Closing would mean killing processes by name (can lose unsaved work) or a custom GNOME extension (fragile across updates). Excluded from v1 by design; say the word if you want the process-kill version anyway.
2. **Bare "odin" will false-trigger.** It is one syllable and collides with "open", "oh then", "ordering". Default is to require **"hey odin"** or **"hi odin"**; bare "odin" is a config toggle, off by default.
3. **RAM is tight right now** — 10 GB of your 14 GB is already in use, ~3.6 GB available. Vosk small sits around 200 MB RSS, Piper loads on demand. Fine, but worth knowing ODIN is not free.
4. **Internal laptop mic** is the only input. Expect a reliable range of roughly 1–2 m in a quiet room; a loud fan or music playing *through the laptop speakers* will hurt wake-word accuracy more than anything else in this plan.
5. **One-time internet** is needed for `setup.sh` (pip packages + two model files). Everything after that is fully offline — that is a guarantee, not a best effort.
