import unittest
from unittest import mock

from tempo_tracker.hotkey import SIGNATURE, HotKey, parse


def hotkey() -> HotKey:
    """A HotKey with Carbon mocked out: nothing is registered with macOS in tests."""
    key = HotKey.__new__(HotKey)
    key._carbon = mock.Mock()
    key._carbon.RegisterEventHotKey.return_value = 0
    key._callbacks, key._refs = {}, {}
    return key


class HotKeyTest(unittest.TestCase):
    def test_each_slot_calls_its_own_callback(self):
        key, pressed = hotkey(), []
        key.register("ctrl+alt+t", lambda: pressed.append("switch"), 1)
        key.register("ctrl+alt+j", lambda: pressed.append("open"), 2)
        key._dispatch(2)
        key._dispatch(1)
        key._dispatch(3)  # unknown slot: nothing
        self.assertEqual(["open", "switch"], pressed)

    def test_the_slot_is_the_hot_key_id(self):
        key = hotkey()
        key.register("ctrl+alt+j", lambda: None, 2)
        hot_key_id = key._carbon.RegisterEventHotKey.call_args.args[2]
        self.assertEqual((SIGNATURE, 2), (hot_key_id.signature, hot_key_id.id))
        self.assertEqual(parse("ctrl+alt+j")[:2], key._carbon.RegisterEventHotKey.call_args.args[:2])

    def test_unregistering_one_slot_keeps_the_other(self):
        key, pressed = hotkey(), []
        key.register("ctrl+alt+t", lambda: pressed.append("switch"), 1)
        key.register("ctrl+alt+j", lambda: pressed.append("open"), 2)
        key.unregister(2)
        key._dispatch(2)
        key._dispatch(1)
        self.assertEqual(["switch"], pressed)
        key.unregister()
        self.assertEqual({}, key._refs)

    def test_a_taken_shortcut_is_not_registered(self):
        key = hotkey()
        key._carbon.RegisterEventHotKey.return_value = -9878  # eventHotKeyExistsErr
        self.assertFalse(key.register("ctrl+alt+j", lambda: None, 2))
        self.assertEqual({}, key._callbacks)
