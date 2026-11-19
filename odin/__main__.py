"""ODIN command line.

    odin run           start the always-on daemon
    odin check         validate dependencies, models, phrases and apps
    odin mic-test      live microphone level meter
    odin wake-test     show wake-word detections as they happen
    odin listen-test   show recognised commands without acting on them
    odin do "<text>"   run a command by text, with no voice involved
    odin say "<text>"  speak something
    odin devices       list audio input devices
    odin phrases       list everything ODIN understands
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path

from .config import Config

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

OK = "\033[32mOK\033[0m"
BAD = "\033[31mFAIL\033[0m"
WARN = "\033[33mWARN\033[0m"


def setup_logging(cfg: Config, override: str | None = None) -> None:
    level = _LEVELS.get(override or cfg["log"]["level"], logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)-14s %(message)s",
        datefmt="%H:%M:%S",
    )


# ------------------------------------------------------------------ check
def cmd_check(args, cfg: Config) -> int:
    rows: list[tuple[str, str, str]] = []
    failures = 0
    warnings = 0

    def record(name: str, status: str, detail: str = "") -> None:
        nonlocal failures, warnings
        rows.append((name, status, detail))
        if status == BAD:
            failures += 1
        elif status == WARN:
            warnings += 1

    # --- python packages ---
    try:
        import sounddevice  # noqa: F401
        record("PortAudio / sounddevice", OK)
    except Exception as exc:
        record("PortAudio / sounddevice", BAD,
               f"{exc} - run: sudo apt install -y libportaudio2")

    for module in ("vosk", "numpy", "jeepney", "piper"):
        try:
            __import__(module)
            record(f"python: {module}", OK)
        except Exception as exc:
            record(f"python: {module}", BAD, str(exc))

    # --- external programs ---
    for program, why, fatal in (
        ("playerctl", "play / pause / next", True),
        ("wpctl", "volume", True),
        ("gtk-launch", "opening apps", True),
        ("systemctl", "suspend / shutdown", True),
        ("brightnessctl", "brightness fallback", False),
    ):
        if shutil.which(program):
            record(f"program: {program}", OK, why)
        else:
            record(f"program: {program}", BAD if fatal else WARN,
                   f"{why} - missing")

    # --- models ---
    if cfg.model_dir.exists():
        record("vosk model", OK, cfg.model_dir.name)
    else:
        record("vosk model", BAD, f"missing: {cfg.model_dir}")
    if cfg.voice_path.exists():
        record("piper voice", OK, cfg.voice_path.name)
    else:
        record("piper voice", WARN, f"missing: {cfg.voice_path} (beeps only)")

    # --- microphone ---
    try:
        import sounddevice as sd

        device = cfg["audio"]["device"] or None
        info = sd.query_devices(device, "input")
        record("microphone", OK, str(info["name"]).strip())
    except Exception as exc:
        record("microphone", BAD, str(exc))

    # --- phrases against the lexicon (the important one) ---
    if cfg.model_dir.exists():
        try:
            from vosk import Model, SetLogLevel

            from .intents import IntentTable, confirm_grammar, wake_grammar
            from .vocab import check_phrases, words_in

            SetLogLevel(-1)
            model = Model(str(cfg.model_dir))
            table = IntentTable(cfg)
            phrases = (
                table.phrases
                + json.loads(wake_grammar(cfg))
                + json.loads(confirm_grammar())
            )
            missing = check_phrases(model, phrases)
            detail = f"{len(table.phrases)} phrases, {len(words_in(phrases))} words"
            if missing:
                record("phrase vocabulary", BAD,
                       "not in lexicon: " + ", ".join(missing))
            else:
                record("phrase vocabulary", OK, detail)

            from .actions import resolve

            unresolved = []
            for intent in table.intents:
                try:
                    resolve(intent.handler)
                except Exception:
                    unresolved.append(intent.name)
            record("intent handlers", BAD if unresolved else OK,
                   ", ".join(unresolved) if unresolved
                   else f"{len(table.intents)} intents")
        except Exception as exc:
            record("phrase vocabulary", BAD, str(exc))

    # --- configured apps ---
    try:
        from .actions.apps import resolve_desktop

        missing_apps = [
            f"{alias} -> {desktop}"
            for alias, desktop in cfg["apps"].items()
            if resolve_desktop(desktop) is None
        ]
        record("configured apps", WARN if missing_apps else OK,
               ", ".join(missing_apps) if missing_apps
               else f"{len(cfg['apps'])} apps resolve")
    except Exception as exc:
        record("configured apps", BAD, str(exc))

    # --- configured folders and pages ---
    try:
        from pathlib import Path as _Path

        missing_folders = []
        for alias, target in cfg.get("folders", {}).items():
            if target.startswith(("http://", "https://", "trash://", "file://")):
                continue
            if not _Path(target).expanduser().is_dir():
                missing_folders.append(f"{alias} -> {target}")
        folders = len(cfg.get("folders", {}))
        pages = len(cfg.get("web", {}))
        record("configured places", WARN if missing_folders else OK,
               "missing: " + ", ".join(missing_folders) if missing_folders
               else f"{folders} folders, {pages} pages")
    except Exception as exc:
        record("configured places", BAD, str(exc))

    # --- vlc subtitle control ---
    # A config check, not a liveness probe: VLC does not have to be running.
    try:
        from .actions import Context
        from .actions.vlc import (PASSWORD_KEY, VLCRC,
                                  status as vlc_status,
                                  vlc_is_running, vlcrc_password)

        if not VLCRC.exists():
            record("VLC subtitles", WARN, f"no {VLCRC} yet - run VLC once")
        else:
            text = VLCRC.read_text(encoding="utf-8", errors="replace")
            enabled = any(
                line.startswith("extraintf=") and "http" in line
                for line in text.splitlines()
            )
            password = cfg["vlc"]["password"] or vlcrc_password()
            if not enabled or not password:
                want = ("extraintf=http" if not enabled
                        else f"{PASSWORD_KEY}=<something>")
                record("VLC subtitles", WARN,
                       f"add {want} to {VLCRC}")
            else:
                where = f"{cfg['vlc']['host']}:{cfg['vlc']['port']}"
                # If VLC is up, say whether it is actually reachable: a
                # second VLC cannot bind the port and ends up with no
                # control interface at all, which is otherwise baffling.
                if not vlc_is_running():
                    record("VLC subtitles", OK, f"{where} (VLC not running)")
                else:
                    try:
                        vlc_status(Context(cfg=cfg))
                        record("VLC subtitles", OK, f"{where}, VLC responding")
                    except Exception:
                        record("VLC subtitles", WARN,
                               f"VLC is running but {where} does not answer"
                               " - a second VLC, or restart VLC")
    except Exception as exc:
        record("VLC subtitles", WARN, str(exc))

    # --- brightness writability ---
    try:
        from .actions.system import _backlight, brightness_percent

        device = _backlight()
        target = device / "brightness"
        percent = brightness_percent()
        import os

        if os.access(target, os.W_OK):
            record("brightness", OK, f"currently {percent}%")
        elif shutil.which("brightnessctl"):
            record("brightness", WARN, "via brightnessctl (may need video group)")
        else:
            record("brightness", WARN,
                   "read-only - run: sudo scripts/setup-system.sh")
    except Exception as exc:
        record("brightness", WARN, str(exc))

    # --- print ---
    width = max(len(name) for name, _, _ in rows)
    print()
    for name, status, detail in rows:
        print(f"  {name.ljust(width)}  {status}  {detail}")
    print()
    if failures:
        print(f"  {failures} blocking problem(s), {warnings} warning(s).")
    else:
        print(f"  Ready. {warnings} warning(s).")
    print()
    return 1 if failures else 0


# -------------------------------------------------------------------- run
def cmd_run(args, cfg: Config) -> int:
    from .daemon import Odin

    odin = Odin(cfg, announce=not args.quiet)
    odin.install_signal_handlers()
    try:
        odin.run()
    except KeyboardInterrupt:
        pass
    return 0


# --------------------------------------------------------------- mic-test
def cmd_mic_test(args, cfg: Config) -> int:
    from .audio import Microphone, rms

    seconds = args.seconds
    print(f"Speak - showing input level for {seconds:.0f}s "
          f"(threshold {cfg['listen']['energy_threshold']}).  Ctrl-C to stop.\n")
    threshold = float(cfg["listen"]["energy_threshold"])
    peak = 0.0
    with Microphone(
        device=cfg["audio"]["device"] or None,
        sample_rate=cfg["audio"]["sample_rate"],
        block_ms=cfg["audio"]["block_ms"],
    ) as mic:
        started = time.monotonic()
        try:
            while time.monotonic() - started < seconds:
                block = mic.read(timeout=0.5)
                if block is None:
                    continue
                level = rms(block)
                peak = max(peak, level)
                bars = int(min(level, 4000) / 4000 * 50)
                flag = "SPEECH" if level >= threshold else "      "
                print(f"\r  {flag} |{'#' * bars}{' ' * (50 - bars)}| "
                      f"{level:7.0f}", end="", flush=True)
        except KeyboardInterrupt:
            pass
    print(f"\n\n  peak level: {peak:.0f}")
    if peak < threshold:
        print("  Nothing above the speech threshold was heard - check that the"
              "\n  right input device is selected and not muted.")
    return 0


# -------------------------------------------------------------- wake-test
def cmd_wake_test(args, cfg: Config) -> int:
    from .audio import Microphone
    from .daemon import load_model
    from .intents import wake_grammar, wake_phrases
    from .wake import VoskWakeEngine

    model = load_model(cfg)
    engine = VoskWakeEngine(
        model, wake_grammar(cfg), phrases=wake_phrases(cfg),
        sample_rate=cfg["audio"]["sample_rate"],
        on_partial=cfg["wake"]["on_partial"],
        refractory_s=cfg["wake"]["refractory_s"],
    )
    print(f"\nSay the wake word: {', '.join(wake_phrases(cfg))}")
    print("Every detection is printed below. Ctrl-C to stop.\n")
    hits = 0
    with Microphone(
        device=cfg["audio"]["device"] or None,
        sample_rate=cfg["audio"]["sample_rate"],
        block_ms=cfg["audio"]["block_ms"],
    ) as mic:
        try:
            while True:
                block = mic.read(timeout=0.5)
                if block is None:
                    continue
                event = engine.process(block)
                if event is not None:
                    hits += 1
                    kind = "partial" if event.partial else "final  "
                    print(f"  [{time.strftime('%H:%M:%S')}] WAKE  "
                          f"({kind})  text={event.text!r}")
        except KeyboardInterrupt:
            pass
    print(f"\n  {hits} detection(s).\n")
    return 0


# ------------------------------------------------------------ listen-test
def cmd_listen_test(args, cfg: Config) -> int:
    from .audio import Microphone
    from .daemon import load_model
    from .intents import IntentTable
    from .listen import CommandListener

    model = load_model(cfg)
    table = IntentTable(cfg)
    listener = CommandListener(model, cfg, sample_rate=cfg["audio"]["sample_rate"])
    grammar = table.command_grammar()
    print(f"\nSay commands directly (no wake word). {len(table.phrases)} known "
          "phrases.\nNothing is executed. Ctrl-C to stop.\n")
    with Microphone(
        device=cfg["audio"]["device"] or None,
        sample_rate=cfg["audio"]["sample_rate"],
        block_ms=cfg["audio"]["block_ms"],
    ) as mic:
        try:
            while True:
                result = listener.listen(mic, grammar)
                if not result.ok:
                    continue
                match = table.match_any(result.candidates or (result.text,))
                extra = ""
                if len(result.candidates) > 1:
                    extra = f"   (also heard: {', '.join(result.candidates[1:])})"
                if match is None:
                    print(f"  heard {result.text!r} -> no intent{extra}")
                else:
                    print(f"  heard {result.text!r} -> {match.intent.name} "
                          f"{match.slots or ''}{extra}")
        except KeyboardInterrupt:
            pass
    print()
    return 0


# --------------------------------------------------------------------- do
def cmd_do(args, cfg: Config) -> int:
    from .actions import ActionError, Context, dispatch
    from .intents import IntentTable

    table = IntentTable(cfg)
    phrase = " ".join(args.phrase).lower().strip()
    match = table.match(phrase)
    if match is None:
        print(f"No intent matches {phrase!r}. Try: odin phrases")
        return 1
    ctx = Context(cfg=cfg)
    print(f"{match.intent.name} {match.slots or ''}")
    try:
        spoken = dispatch(match.intent.handler, ctx, match.slots)
    except ActionError as exc:
        print(f"failed: {exc}")
        return 1
    reply = spoken if spoken is not None else match.intent.reply
    print(f"-> {reply or 'done'}")
    return 0


# -------------------------------------------------------------------- say
def cmd_say(args, cfg: Config) -> int:
    from .speech import Speaker

    Speaker(cfg).say(" ".join(args.text))
    return 0


def cmd_devices(args, cfg: Config) -> int:
    import sounddevice as sd

    print()
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0:
            default = " (default)" if index == sd.default.device[0] else ""
            print(f"  [{index}] {device['name']}{default}")
    print("\n  Set the one you want as device = \"<name or index>\" "
          "in config.toml.\n")
    return 0


def cmd_phrases(args, cfg: Config) -> int:
    from .intents import IntentTable

    table = IntentTable(cfg)
    by_intent: dict[str, list[str]] = {}
    for intent in table.intents:
        by_intent.setdefault(intent.name, []).extend(intent.phrases)
    print()
    for name in sorted(by_intent):
        print(f"  {name}")
        for phrase in by_intent[name]:
            print(f"      \"{phrase}\"")
    print(f"\n  {len(table.intents)} intents, {len(table.phrases)} phrases.\n")
    return 0


# ------------------------------------------------------------------ main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="odin",
        description="ODIN - offline voice assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-c", "--config", help="path to config.toml")
    parser.add_argument("-v", "--verbose", action="store_const",
                        const="debug", dest="log_level",
                        help="debug logging")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start the always-on daemon")
    run.add_argument("--quiet", action="store_true",
                     help="do not announce startup")
    run.set_defaults(func=cmd_run)

    sub.add_parser("check", help="validate the installation").set_defaults(
        func=cmd_check)

    mic = sub.add_parser("mic-test", help="live input level meter")
    mic.add_argument("--seconds", type=float, default=15.0)
    mic.set_defaults(func=cmd_mic_test)

    sub.add_parser("wake-test", help="show wake-word detections").set_defaults(
        func=cmd_wake_test)
    sub.add_parser("listen-test", help="recognise commands without acting"
                   ).set_defaults(func=cmd_listen_test)

    do = sub.add_parser("do", help="run a command by text")
    do.add_argument("phrase", nargs="+")
    do.set_defaults(func=cmd_do)

    say = sub.add_parser("say", help="speak some text")
    say.add_argument("text", nargs="+")
    say.set_defaults(func=cmd_say)

    sub.add_parser("devices", help="list audio input devices").set_defaults(
        func=cmd_devices)
    sub.add_parser("phrases", help="list everything ODIN understands"
                   ).set_defaults(func=cmd_phrases)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    cfg = Config.load(args.config)
    setup_logging(cfg, args.log_level)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
