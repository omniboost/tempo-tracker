"""Tests mogen nooit een echt venster openen: dat blijft wachten op een klik en de testrun hangt."""

import requests

from tempo_tracker import calls, dialogs, idle, ui


def _no_real_windows(*args, **kwargs):
    raise AssertionError("A test opened a real window; mock the dialogs function instead.")


def _no_network(*args, **kwargs):
    raise requests.ConnectionError("A test tried to reach a real server; mock the client instead.")


def _inline_progress(title, text, work):
    """Het werk achter de voortgangskaart gewoon direct uitvoeren, zonder venster."""
    return work(lambda line: None)


ui.run_modal = _no_real_windows
dialogs.progress = _inline_progress
# Tests gaan uit van een scherm dat aan staat en geen gesprek, niet van de echte Mac.
idle.screen_off = lambda: False
calls.in_call = lambda: False
# Geen echt netwerk: een vergeten mock geeft een verbindingsfout in plaats van verkeer naar Jira of Tempo.
requests.Session.request = _no_network
requests.get = _no_network
