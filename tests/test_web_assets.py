"""Structural checks on the browser assets.

The Python tests exercise the JSON API, but the interface is only useful if the
page is wired to it correctly. A renamed element id or a handler bound to a
control that no longer exists produces a page that loads cleanly and does
nothing, which is the worst possible failure during a timed draft.

These tests are deliberately structural rather than behavioural: they assert
that every element the script reaches for exists in the markup, that every
endpoint the script calls exists on the server, and that the safety-critical
affordances are present.
"""

from __future__ import annotations

import pathlib
import re
import unittest

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text()
JS = (WEB / "app.js").read_text()
CSS = (WEB / "style.css").read_text()


def html_ids() -> set[str]:
    return set(re.findall(r'id="([^"]+)"', HTML))


def js_referenced_ids() -> set[str]:
    return set(re.findall(r"\$\('([^']+)'\)", JS))


class TestWiring(unittest.TestCase):
    def test_every_id_the_script_uses_exists_in_the_page(self):
        missing = js_referenced_ids() - html_ids()
        self.assertEqual(missing, set(), f"script reaches for absent elements: {missing}")

    def test_every_endpoint_called_exists_on_the_server(self):
        from ffopt import webapp  # noqa: F401  (import validates the module loads)
        source = (pathlib.Path(__file__).resolve().parent.parent
                  / "ffopt" / "webapp.py").read_text()
        called = set(re.findall(r"'(/api/[a-z]+)'", JS))
        self.assertTrue(called, "no API calls found; the script is probably broken")
        for endpoint in called:
            self.assertIn(f'"{endpoint}"', source, f"{endpoint} has no server route")

    def test_assets_are_linked_from_the_page(self):
        self.assertIn('href="/style.css"', HTML)
        self.assertIn('src="/app.js"', HTML)


class TestSafetyAffordances(unittest.TestCase):
    """Controls whose absence would be dangerous mid-draft."""

    def test_panic_button_exists_and_is_bound(self):
        self.assertIn('id="panic"', HTML)
        self.assertIn("$('panic').onclick", JS)

    def test_undo_exists_and_is_bound(self):
        self.assertIn('id="undo"', HTML)
        self.assertIn("$('undo').onclick", JS)

    def test_correction_dialog_exists(self):
        for element in ("fixDialog", "fixQuery", "fixApply", "fixRemove", "fixCancel"):
            self.assertIn(f'id="{element}"', HTML)

    def test_reset_requires_confirmation(self):
        """Reset destroys the board; it must not be a single stray click."""
        self.assertIn("confirm(", JS)

    def test_low_value_options_are_flagged(self):
        """The trap this project exists to avoid must be visible in the list."""
        self.assertIn("low value", JS)
        self.assertIn(".picks li.weak", CSS)

    def test_stale_list_cannot_be_clicked(self):
        """Guards against claiming a player from a list that is being refreshed."""
        self.assertIn("setPicksStale", JS)
        self.assertIn(".picks.stale", CSS)
        self.assertIn("pointer-events: none", CSS)

    def test_requests_have_a_timeout(self):
        """A hung request during a 60-second pick window is a failure."""
        self.assertIn("AbortController", JS)
        self.assertIn("timeoutMs", JS)

    def test_user_content_is_escaped(self):
        """Player names reach innerHTML; they must all be escaped.

        Checks the invariant rather than one spelling of it: every template
        interpolation that mentions a name-bearing field has to be wrapped in
        escapeHtml. An earlier version asserted a single literal call, which
        silently stopped covering anything the moment the call was renamed.
        """
        self.assertIn("function escapeHtml", JS)

        # ${ ... } interpolations referencing a name field.
        interps = re.findall(r"\$\{([^}]*(?:\.name|\.short|disp\()[^}]*)\}", JS)
        self.assertGreater(len(interps), 4, "expected several name interpolations")
        unescaped = [x for x in interps if "escapeHtml" not in x]
        self.assertEqual(unescaped, [], f"unescaped name interpolations: {unescaped}")

    def test_keyboard_shortcuts_documented_in_the_page(self):
        self.assertIn("P = panic", HTML)
        self.assertIn("U = undo", HTML)

    def test_recording_a_pick_is_click_first(self):
        """A mock draft was lost typing entries during an opponent burst.

        Every path that commits a pick must be a click on a name the operator
        can see. Submitting a raw query and letting the server resolve it
        out of sight is what recorded the wrong player.
        """
        self.assertIn('id="quick"', HTML)
        self.assertIn("renderQuick", JS)
        self.assertNotIn("claimByQuery", JS)
        self.assertIn("/api/suggest", JS)

    def test_typing_searches_as_you_go(self):
        """The box must show matches live, not only on submit."""
        self.assertIn("onQueryInput", JS)
        self.assertIn("'input'", JS)

    def test_names_render_in_the_platforms_format(self):
        """Short names are what makes this screen match the draft room."""
        self.assertIn("p.short", JS)

    def test_modes_offered_match_the_backend(self):
        from ffopt import session
        for mode in session.MODES:
            self.assertIn(f'value="{mode}"', HTML)


class TestNoExternalDependencies(unittest.TestCase):
    """The interface must work with no network and no install step."""

    def test_no_remote_assets(self):
        for pattern in ("http://", "https://", "//cdn", "integrity="):
            self.assertNotIn(pattern, HTML, f"page references remote asset: {pattern}")

    def test_no_build_step_artifacts(self):
        self.assertNotIn("import ", JS.split("\n")[0], "app.js must be a plain script")
        self.assertNotIn("require(", JS)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPollingBehaviour(unittest.TestCase):
    """Polling must be responsive near our turn without hammering the API."""

    def test_polling_is_adaptive(self):
        self.assertIn("function pollInterval", JS)
        self.assertIn("picks_until_my_turn", JS)

    def test_polling_uses_a_self_rescheduling_timeout(self):
        """setInterval would stack requests if one sync ran long."""
        self.assertIn("setTimeout(tick", JS)
        self.assertNotIn("setInterval", JS)

    def test_polling_slows_down_when_the_draft_is_over(self):
        self.assertIn("s.complete) return 10000", JS)

    def test_manual_mode_never_polls(self):
        self.assertIn("mode !== 'manual'", JS)

    def test_board_drift_is_detected_and_repairable(self):
        """A drifted board is silent and compounds; it must be surfaced.

        A rehearsal lost a pick this way: an entry was missed, every later
        pick shifted by one, and the tool went on recommending players who
        were already gone until the operator's own turn arrived.
        """
        self.assertIn("checkAlignment", JS)
        self.assertIn("/api/alignment", JS)
        self.assertIn("/api/adopt", JS)
        self.assertIn('id="drift"', HTML)
        self.assertIn('id="driftFix"', HTML)

    def test_dismissing_drift_does_not_mute_it_forever(self):
        """A permanently silenced alarm is worse than no alarm."""
        self.assertIn("signature", JS)
        self.assertIn("App.driftIgnored === signature", JS)

    def test_the_platform_pick_label_is_displayed(self):
        """The one number visible on both screens, so drift is a glance."""
        self.assertIn('id="pickLabel"', HTML)
        self.assertIn("pick_label", JS)

    def test_whose_pick_is_being_recorded_is_stated(self):
        """Clicking means different things one pick apart."""
        self.assertIn('id="whoFor"', HTML)
        self.assertIn("Recording YOUR pick", JS)

    def test_live_mode_has_a_stalled_feed_check(self):
        """In live mode the drift check is tautological.

        The board *is* the feed there, so the two always agree and the drift
        warning can never fire. A feed that quietly stops publishing looks
        exactly like "nobody has picked yet", so it must be caught by elapsed
        time instead.
        """
        self.assertIn("checkFeedStalled", JS)
        self.assertIn("pick_timer", JS)
        self.assertIn("gone quiet", JS)

    def test_the_stalled_check_is_skipped_in_manual(self):
        """Manual mode never reads the feed, so a quiet feed means nothing."""
        block = JS[JS.index("function checkFeedStalled"):]
        block = block[:block.index("async function checkAlignment")]
        self.assertIn("s.mode === 'manual'", block)
