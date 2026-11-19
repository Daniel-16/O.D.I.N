# ODIN

**O**ffline **D**aemon for **I**ntent **N**avigation

An offline, voice-only assistant for Linux Ubuntu only. No cloud, no account, no
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
| Subtitles | subtitle · subtitles · next subtitle  *(cycles VLC's subtitle track; see below)* |
| Volume | volume up · volume down · louder · quieter · mute · sound on · max volume · set volume to sixty percent · half volume |
| Apps | open firefox / chrome / spotify / telegram / terminal / files / settings / calculator / code / media player / whats app web / youtube music / … (also "launch …" or "start …") |
| Folders | open series · open movies · open videos · open downloads · open documents · open pictures · open screenshots · open music folder · open home folder · open trash |
| Web | open youtube · open gmail · open maps · open github |
| System | lock the screen · suspend · take a screenshot · brightness up · brightness down · what time is it · what day is it · battery level |
| Questions | whats playing · am i muted · what is the volume · what can you do · say that again |
| Correcting | undo that · never mind |
| Power | shut down · reboot  *(both ask "Are you sure?" and need a spoken "yes")* |
| ODIN | stop listening · start listening · are you there |

### Subtitles in VLC

Saying *"subtitle"* cycles VLC's subtitle track, exactly as pressing **V** in
VLC does — including cycling round to "off". Wayland forbids synthetic
keypresses, so ODIN does not press the key: it asks VLC to run the action that
key is bound to, over VLC's own HTTP control interface on loopback. Nothing
leaves the machine.

That interface has to be switched on once, in `~/.config/vlc/vlcrc`:

```ini
extraintf=http
http-host=127.0.0.1
http-port=8080
http-password=<something>
```

Only one VLC can hold the port: if a second VLC is opened while the first is
still playing, the second starts with no control interface at all, and
*"subtitle"* answers *"VLC's web interface is not responding."* Close the
spare VLC window and the next one to start gets the port. `./odin.sh check`
says which of the two situations you are in.

`./odin.sh check` reports whether that is in place, and ODIN picks the password
up from `vlcrc` on its own — set `[vlc] password` in `config.toml` only if you
want to override it. ODIN never writes to `vlcrc` itself. With VLC closed, the
command simply answers *"VLC is not running."*

`./odin.sh phrases` prints the authoritative list. To add an app, add a line to
`[apps]` in `config.toml` and run `./odin.sh check` — it will tell you if the
words you chose can be recognised.

## How it talks back

**One wake word, several commands.** After acting, ODIN keeps listening for
`listen.follow_up_s` seconds, so you can say *"hey odin"* → *"pause"* →
*"volume up"* without waking it again. One caveat: **you cannot talk over its
reply.** The microphone is muted while ODIN speaks, so that it never
transcribes its own voice — wait for it to finish before the next command.

**It asks when it nearly understood.** Say *"lock"* and it answers *"Did you
mean lock screen?"*; say something between two commands and it offers both:
*"Did you mean brightness up, or brightness down?"* A spoken "yes" accepts a
single suggestion, or name the one you want. When too many commands would fit —
a bare *"open"* matches dozens — it says it did not catch that rather than
guessing, and it never suggests anything destructive.

**Two levels of chat.** `speech.verbosity = "chatty"` confirms ordinary actions
out loud (*"Paused."*, *"Volume 70 percent."*). `"brief"` beeps for those
instead and saves the voice for answers, errors and clarifications — questions
like *"battery level"* are always spoken, because the reply *is* the answer.

**It can take one step back.** *"undo that"* reverses the last volume or
brightness change. *"say that again"* repeats its last reply.

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

* **The wake grammar is wider than the wake word.** A Vosk grammar is a bigram
  model over only the words it contains. With nothing but {hey, hi, odin} in
  it, "hey" has no legal continuation except "odin" — so a bare "hey", or "hey
  there", or "okay", was decoded as "hey odin" and woke ODIN. The grammar
  therefore also carries ~110 decoy words that give the decoder somewhere else
  to go. Decoys never wake ODIN: `wake_phrases()` is the match set and is
  deliberately separate from the grammar. Add your own with
  `wake.extra_decoys` if some phrase you say often still triggers it.
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
| subtitle | VLC's HTTP interface, hotkey `key-subtitle-track` | MPRIS has no subtitle control, and no synthetic `V` key reaches a Wayland window |
| volume | `wpctl` (WirePlumber) | PipeWire owns the sink, not ALSA |
| open app | `gtk-launch` in a transient systemd scope | starts the app as a click would, *outside* ODIN's control group (see below) |
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
| `speech.verbosity` | `chatty` confirms actions aloud, `brief` beeps instead |
| `listen.follow_up_s` | how long to keep listening after acting (0 disables chaining) |
| `listen.follow_up_threshold_scale` | how much louder speech must be in an unprompted follow-up window |
| `listen.max_turns` | most commands accepted from one wake word |
| `listen.clarify` / `clarify_cutoff` | whether to offer "did you mean …", and how close a match must be |
| `folders` / `web` | spoken alias → folder path or URL |
| `wake.extra_decoys` | extra words the wake recogniser may hear instead of the wake word |
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

Three knobs matter most: `listen.energy_threshold` and `wake.min_level` for
your room, and `wake.allow_bare_name` for how eager ODIN is.

**Set the two level knobs from measurements, not by feel.** Both must sit
above your room's noise floor, or ODIN treats the room as speech. On the
machine this was developed on, `mic-test` reads about 570 in a quiet room,
920 with a film playing (peaks near 2000) and 5700 for a spoken phrase (peaks
near 15800) — hence the shipped 800. If `energy_threshold` is below your
floor, ODIN decodes whatever is playing into commands; if it is far above
your voice, nothing is heard at all.

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
trigger anything destructive. It is also what caught two bugs in the
conversation features: with a follow-up window open, the wake word itself
reached the *command* recogniser, came back as "louder" and changed the volume
(the wake phrases are now part of the command grammar and any hypothesis
containing one is treated as a re-wake), and room noise in that same window
could trigger a command (hence the loudness scaling). That test is what caught the one timing bug
found in development: the wake word is detected on a *partial* result, part-way
through "hey odin", so the tail of the wake word was still arriving and landed
in the command recogniser — which then ended the command window before the
speaker had said anything. The fix is in two parts: `wake.settle_ms` discards
the tail, and the listener now treats an utterance that yields no usable
command as a false start and keeps listening instead of giving up.

Neither test says anything about how well ODIN hears *you* — that is what
`wake-test` is for.

## Apps ODIN opens are not ODIN's children

systemd kills every process in a unit's control group when that unit stops, and
a child process inherits its parent's group. Apps launched by ODIN were
therefore being killed the instant ODIN restarted — and their memory counted
against ODIN's 2 GB cap, putting the whole group at risk of an OOM kill.

Two changes fix it, and both are needed:

* every launch goes through `systemd-run --user --scope`, so the app gets its
  own transient scope outside `odin.service` (see `spawn_detached` in
  `odin/actions/__init__.py`);
* `KillMode=process` in the unit, so stopping ODIN kills only ODIN. This is the
  backstop for anything that still ends up in the group.

To check it yourself, launch something by voice and look at where it landed:

```bash
systemctl --user status odin        # should list only the python process
```

## Limits, stated plainly

* **No window control.** "Close Firefox" or "switch to Chrome" are not offered:
  GNOME on Wayland deliberately blocks scripted window control. Launching apps
  works perfectly. The alternatives would be killing processes by name (risks
  unsaved work) or a custom GNOME extension (breaks on updates).
* **Bare "odin" is off by default** — one syllable, and it collides with
  "open". Use "hey odin" or "hi odin", or turn it on in `config.toml`.
* **A true homophone still gets through.** "they owe him" sounds almost
  exactly like "hey odin" and wakes it. Nothing in a closed-grammar design can
  separate those two, and a person would mishear them too.
* **Range is about 1–2 m** on the internal microphone in a quiet room. The
  biggest enemy of wake-word accuracy is music playing through the laptop's own
  speakers. ODIN mutes its own microphone while it speaks, but it cannot do
  that for your music, so it instead requires you to be **louder than whatever
  is playing**: a detection must clear `wake.min_level` and be
  `wake.speak_up_margin` times the background level ODIN has settled on. A
  film becomes the background and stops waking it; you talking over the film
  still does. The cost is that ODIN cannot be woken by a whisper while
  something loud is playing. Set `wake.min_level = 0` to switch the check off.
* **Brightness needs the udev rule** from `scripts/setup-system.sh`. Without
  it, ODIN says so instead of failing silently.
* **You cannot speak over ODIN's reply.** Its microphone is muted while it
  talks, so a command said during a spoken reply is lost. Set
  `speech.verbosity = "brief"` if you want shorter gaps between commands.
* **A follow-up window is held to a stricter standard.** Only its best
  hypothesis may match a command, it never asks "did you mean ...?", and
  anything that is not a command closes the window. Before that, a film's
  dialogue reached it as "files" (-> pause), "viewer" (-> dimmer) and "set"
  (-> silence), and ODIN skipped tracks on its own. Set
  `listen.follow_up_strict = false` to go back to the old behaviour.
* **ODIN recognises its own voice coming back.** Saying "next" was answered
  with "Next.", which the open follow-up window heard and acted on, skipping
  twice. Words it has just spoken are ignored for `listen.echo_guard_s`.
* **A follow-up window asks for a clearer voice.** Nobody has addressed ODIN
  during one, so room noise gets a chance to be decoded — live testing had
  noise come back as "louder" and change the volume. Speech must now be about
  twice as loud to count there, which means a chained command needs to be
  spoken deliberately, not muttered.
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

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and pull request guidelines.

## License

ODIN is licensed under the [MIT License](LICENSE).

## Adding your own commands

Apps, folders and web pages are config, not code. Add a line to `[apps]`,
`[folders]` or `[web]` in `config.toml`, run `./odin.sh check`, and restart.
Anything else — a new kind of command — goes in `odin/intents.py`.

There is one rule that catches everyone out: **ODIN can only hear words that
are already in the speech model's lexicon.** A word that is not in it is
silently dropped from the grammar, so the phrase containing it can never be
recognised. `./odin.sh check` validates every phrase against the lexicon and
names any word that is missing, which is why it is worth running after every
edit.

You cannot add words to the model. Vosk's own documentation is explicit about
it: runtime grammars and language-model rebuilds both select from the existing
vocabulary, and introducing a new word means recompiling the graph from a
pronunciation dictionary — which Vosk only supports for some of its large
models, not the small one used here.

What you do instead is spell the word with words the model *does* know. The
spoken alias never has to match the real spelling — it only has to map to an
intent. "whatsapp" is not in the lexicon, but "whats" and "app" both are, so
`"whats app web"` works and opens the WhatsApp Web app.

To see the whole vocabulary:

```bash
sudo apt install libfst-tools
fstsymbols --save_osymbols=words.txt \
    models/vosk-model-small-en-us-0.15/graph/Gr.fst
```
