"""Action tests. External programs and sysfs are stubbed, so these run
anywhere and assert on behaviour rather than on this machine's state.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odin.actions import ActionError, Context
from odin.actions import media, meta, places, system, vlc, volume
from odin.config import Config


def ctx() -> Context:
    return Context(cfg=Config({"apps": {}, "folders": {}, "web": {}}))


class TestPlaces(unittest.TestCase):
    def setUp(self):
        self.opened: list[tuple[str, str]] = []
        self._real = places._open
        places._open = lambda target, label: self.opened.append((target, label))

    def tearDown(self):
        places._open = self._real

    def test_existing_folder_is_opened(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = places.open_folder(ctx(), {"path": tmp, "label": "series"})
            self.assertEqual(self.opened, [(tmp, "series")])
            self.assertIn("series", reply)

    def test_missing_folder_is_reported_not_opened(self):
        with self.assertRaises(ActionError) as caught:
            places.open_folder(ctx(), {"path": "/no/such/dir",
                                       "label": "series"})
        self.assertIn("series", str(caught.exception))
        self.assertEqual(self.opened, [])

    def test_a_file_is_not_a_folder(self):
        with tempfile.NamedTemporaryFile() as handle:
            with self.assertRaises(ActionError):
                places.open_folder(ctx(), {"path": handle.name,
                                           "label": "notes"})

    def test_uri_targets_skip_the_existence_check(self):
        places.open_folder(ctx(), {"path": "trash:///", "label": "trash"})
        self.assertEqual(self.opened, [("trash:///", "trash")])

    def test_home_shorthand_is_expanded(self):
        places.open_folder(ctx(), {"path": "~", "label": "home folder"})
        self.assertEqual(self.opened[0][0], str(Path.home()))

    def test_url_is_opened(self):
        places.open_url(ctx(), {"url": "https://example.com", "label": "site"})
        self.assertEqual(self.opened, [("https://example.com", "site")])

    def test_missing_configuration_is_reported(self):
        with self.assertRaises(ActionError):
            places.open_folder(ctx(), {"label": "series"})
        with self.assertRaises(ActionError):
            places.open_url(ctx(), {"label": "site"})


class TestVolumeParsing(unittest.TestCase):
    def setUp(self):
        self._real = volume.run_command

    def tearDown(self):
        volume.run_command = self._real

    def stub(self, output):
        volume.run_command = lambda argv, timeout=5.0: output

    def test_percent_is_read(self):
        self.stub("Volume: 0.73")
        self.assertEqual(volume.current_percent(ctx()), 73)

    def test_mute_flag_is_detected(self):
        self.stub("Volume: 0.73 [MUTED]")
        self.assertTrue(volume.is_muted(ctx()))
        self.stub("Volume: 0.73")
        self.assertFalse(volume.is_muted(ctx()))

    def test_status_mentions_mute(self):
        self.stub("Volume: 0.40 [MUTED]")
        self.assertIn("muted", volume.status(ctx(), {}).lower())

    def test_unreadable_volume_is_an_error_not_a_crash(self):
        def boom(argv, timeout=5.0):
            raise ActionError("wpctl is not installed")

        volume.run_command = boom
        self.assertIsNone(volume.current_percent(ctx()))
        with self.assertRaises(ActionError):
            volume.status(ctx(), {})

    def test_snapshot_records_state_for_undo(self):
        self.stub("Volume: 0.55")
        c = ctx()
        volume._snapshot(c)
        self.assertEqual(c.undo["kind"], "volume")
        self.assertEqual(c.undo["percent"], 55)


class TestUndo(unittest.TestCase):
    def test_nothing_to_undo_is_reported(self):
        with self.assertRaises(ActionError):
            meta.undo(ctx(), {})

    def test_volume_undo_restores_and_clears(self):
        restored: list[dict] = []
        real = volume.restore
        volume.restore = lambda c, state: restored.append(state)
        try:
            c = ctx()
            c.undo = {"kind": "volume", "percent": 30, "muted": False,
                      "describe": "volume 30 percent"}
            reply = meta.undo(c, {})
        finally:
            volume.restore = real
        self.assertEqual(restored[0]["percent"], 30)
        self.assertIsNone(c.undo)  # one level only, not a stack
        self.assertIn("volume 30 percent", reply)

    def test_brightness_undo_restores(self):
        restored: list[int] = []
        real = system.restore_brightness
        system.restore_brightness = lambda c, s: restored.append(s["percent"])
        try:
            c = ctx()
            c.undo = {"kind": "brightness", "percent": 70,
                      "describe": "brightness 70 percent"}
            meta.undo(c, {})
        finally:
            system.restore_brightness = real
        self.assertEqual(restored, [70])

    def test_unknown_kind_is_refused(self):
        c = ctx()
        c.undo = {"kind": "teleport"}
        with self.assertRaises(ActionError):
            meta.undo(c, {})


class TestMetaReplies(unittest.TestCase):
    def test_repeat_needs_something_said(self):
        with self.assertRaises(ActionError):
            meta.repeat(ctx(), {})

    def test_repeat_returns_the_last_reply(self):
        class Speaker:
            last_said = "Volume 70 percent."

        c = ctx()
        c.speaker = Speaker()
        self.assertEqual(meta.repeat(c, {}), "Volume 70 percent.")

    def test_capabilities_counts_what_is_configured(self):
        c = Context(cfg=Config({
            "apps": {"firefox": "f", "chrome": "c"},
            "folders": {"series": "~/Videos/Series"},
            "web": {"youtube": "https://youtube.com"},
        }))
        reply = meta.capabilities(c, {})
        self.assertIn("2 apps", reply)
        self.assertIn("1 folders", reply)

    def test_standby_and_resume_flip_the_flag(self):
        c = ctx()
        meta.standby(c, {})
        self.assertTrue(c.standby)
        meta.resume(c, {})
        self.assertFalse(c.standby)


class TestNowPlaying(unittest.TestCase):
    def setUp(self):
        self._real = media._playerctl

    def tearDown(self):
        media._playerctl = self._real

    def stub(self, output):
        media._playerctl = lambda ctx_, *args: output

    def test_title_and_artist(self):
        self.stub("Playing|Africa|Toto")
        self.assertEqual(media.now_playing(ctx(), {}), "Playing Africa, by Toto.")

    def test_title_only(self):
        self.stub("Playing|Some Stream|")
        self.assertEqual(media.now_playing(ctx(), {}), "Playing Some Stream.")

    def test_paused_is_said(self):
        self.stub("Paused|Africa|Toto")
        self.assertIn("Paused", media.now_playing(ctx(), {}))

    def test_no_player_is_not_an_error(self):
        def no_player(ctx_, *args):
            raise ActionError(media.NO_PLAYER)

        media._playerctl = no_player
        self.assertEqual(media.now_playing(ctx(), {}), "Nothing is playing.")

    def test_enormous_stream_title_is_trimmed(self):
        self.stub("Playing|" + "x" * 300 + "|")
        self.assertLess(len(media.now_playing(ctx(), {})), 120)


class TestBattery(unittest.TestCase):
    def setUp(self):
        self._real = system._BATTERY_ROOT

    def tearDown(self):
        system._BATTERY_ROOT = self._real

    def fake_battery(self, tmp, capacity, status):
        battery = Path(tmp) / "BAT0"
        battery.mkdir()
        (battery / "capacity").write_text(capacity)
        (battery / "status").write_text(status)
        system._BATTERY_ROOT = Path(tmp)

    def test_level_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_battery(tmp, "87\n", "Discharging\n")
            self.assertEqual(system.battery(ctx(), {}),
                             "Battery 87 percent, on battery.")

    def test_charging(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_battery(tmp, "42", "Charging")
            self.assertIn("charging", system.battery(ctx(), {}))

    def test_unknown_status_still_reports_the_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_battery(tmp, "50", "Weird")
            self.assertEqual(system.battery(ctx(), {}), "Battery 50 percent.")

    def test_no_battery_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            system._BATTERY_ROOT = Path(tmp)
            with self.assertRaises(ActionError):
                system.battery(ctx(), {})


def vlcrc_line(value: str, port: bool = False) -> str:
    """A fake vlcrc fragment.

    The key is composed from the production constant rather than written out
    as a literal: a "<key>=<value>" line in the source is what secret
    scanners flag, fixture or not.
    """
    prefix = "#http-port=8080\n" if port else ""
    return f"{prefix}{vlc.PASSWORD_KEY}={value}\n"


class TestVLCSubtitles(unittest.TestCase):
    """The HTTP boundary is stubbed: no VLC and no socket needed."""

    def setUp(self):
        self._real_open = vlc._opener.open
        self._real_cache = vlc._cached
        self._real_running = vlc.vlc_is_running
        self.requests: list[str] = []

    def tearDown(self):
        vlc._opener.open = self._real_open
        vlc._cached = self._real_cache
        vlc.vlc_is_running = self._real_running

    def stub_open(self, result):
        """Capture the URL asked for; `result` is raised if it is an error."""

        class _Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def read(self_inner):
                return b"<root/>"

        def opened(request, timeout=None):
            self.requests.append(request.full_url)
            if isinstance(result, Exception):
                raise result
            return _Response()

        vlc._opener.open = opened

    def stub_process(self, running: bool):
        vlc.vlc_is_running = lambda: running

    def vlc_ctx(self, password="secret"):
        c = ctx()
        c.cfg["vlc"]["password"] = password
        return c

    def test_subtitle_presses_vlcs_own_v_key(self):
        self.stub_open(None)
        self.assertIsNone(vlc.subtitle(self.vlc_ctx(), {}))
        self.assertEqual(len(self.requests), 1)
        url = self.requests[0]
        self.assertIn("127.0.0.1:8080/requests/status.xml", url)
        self.assertIn("command=key", url)
        # No "key-" prefix: VLC's HTTP interface adds that itself, and the
        # prefixed spelling is answered with 200 and silently ignored.
        self.assertIn("val=subtitle-track", url)
        self.assertNotIn("val=key-subtitle-track", url)

    def test_vlc_not_running_is_said_plainly(self):
        self.stub_open(ConnectionRefusedError(111, "refused"))
        self.stub_process(False)
        with self.assertRaises(ActionError) as caught:
            vlc.subtitle(self.vlc_ctx(), {})
        self.assertEqual(str(caught.exception), vlc.NOT_RUNNING)

    def test_running_vlc_without_an_interface_says_so(self):
        """A second VLC cannot bind the port and has no control interface:
        saying "VLC is not running" there would send the user hunting for
        the wrong problem."""
        self.stub_open(ConnectionRefusedError(111, "refused"))
        self.stub_process(True)
        with self.assertRaises(ActionError) as caught:
            vlc.subtitle(self.vlc_ctx(), {})
        self.assertEqual(str(caught.exception), vlc.NO_INTERFACE)

    def test_rejected_password_is_distinguished(self):
        error = urllib.error.HTTPError(
            "http://127.0.0.1:8080/", 401, "Unauthorized", {}, None)
        self.stub_open(error)
        with self.assertRaises(ActionError) as caught:
            vlc.subtitle(self.vlc_ctx(), {})
        self.assertEqual(str(caught.exception), vlc.BAD_PASSWORD)

    def test_no_password_anywhere_is_not_a_failed_request(self):
        self.stub_open(None)
        vlc._cached = None
        with tempfile.TemporaryDirectory() as tmp:
            real_vlcrc, vlc.VLCRC = vlc.VLCRC, Path(tmp) / "vlcrc"
            try:
                with self.assertRaises(ActionError) as caught:
                    vlc.subtitle(self.vlc_ctx(password=""), {})
            finally:
                vlc.VLCRC = real_vlcrc
        self.assertEqual(str(caught.exception), vlc.NO_PASSWORD)
        self.assertEqual(self.requests, [])

    def test_password_comes_from_vlcrc_when_config_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = Path(tmp) / "vlcrc"
            rc.write_text(vlcrc_line("from-vlcrc", port=True))
            vlc._cached = None
            self.assertEqual(vlc.vlcrc_password(rc), "from-vlcrc")

    def test_config_password_wins_over_vlcrc(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = Path(tmp) / "vlcrc"
            rc.write_text(vlcrc_line("from-vlcrc"))
            real_vlcrc, vlc.VLCRC = vlc.VLCRC, rc
            vlc._cached = None
            try:
                self.assertEqual(vlc._password(self.vlc_ctx("explicit")),
                                 "explicit")
            finally:
                vlc.VLCRC = real_vlcrc


if __name__ == "__main__":
    unittest.main(verbosity=2)
