"""Mengenbegrenzung: zaehlt Erfolge, nicht Fehlversuche.

Die vorhandene Fehlversuchs-Sperre (check/record_failure) hilft dort nicht,
wo alles gelingt und nur zu oft geschieht — der Companion ruft je Anfrage
eine kostenpflichtige API auf. Eine haengende Wiederholschleife im Editor
braucht keinen Angreifer, um Geld zu verbrennen.
"""
import time

from core import ratelimit


def test_allows_up_to_the_limit_then_stops():
    ratelimit.reset("k1")
    assert all(ratelimit.allow("k1", limit=3, window=60) for _ in range(3))
    assert ratelimit.allow("k1", limit=3, window=60) is False


def test_keys_are_independent():
    ratelimit.reset("a"); ratelimit.reset("b")
    assert ratelimit.allow("a", limit=1, window=60)
    assert ratelimit.allow("a", limit=1, window=60) is False
    assert ratelimit.allow("b", limit=1, window=60), "b darf nicht unter a leiden"


def test_window_slides_open_again():
    """Kein starres Fenster: sobald der aelteste Aufruf herausfaellt,
    ist wieder Platz — kein Warten auf einen Blockwechsel."""
    ratelimit.reset("slide")
    assert ratelimit.allow("slide", limit=1, window=1)
    assert ratelimit.allow("slide", limit=1, window=1) is False
    time.sleep(1.05)
    assert ratelimit.allow("slide", limit=1, window=1)


def test_remaining_counts_down():
    ratelimit.reset("r")
    assert ratelimit.remaining("r", limit=2, window=60) == 2
    ratelimit.allow("r", limit=2, window=60)
    assert ratelimit.remaining("r", limit=2, window=60) == 1


def test_quota_does_not_touch_the_failure_lockout():
    """Die zwei Bremsen teilen sich keinen Speicher — sonst sperrte ein
    ausgeschoepftes Kontingent auch noch die Anmeldung."""
    ratelimit.reset("mix")
    ratelimit.clear("mix")
    for _ in range(5):
        ratelimit.allow("mix", limit=99, window=60)
    assert ratelimit.check("mix") == 0, "Erfolge duerfen keine Sperre ausloesen"
