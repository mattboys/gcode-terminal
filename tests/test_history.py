import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "gcode_sender.py"
SPEC = importlib.util.spec_from_file_location("gcode_sender", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class DummyVar:
    def __init__(self):
        self.value = ""

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class TerminalHistoryTests(unittest.TestCase):
    def test_terminal_history_navigation_cycles_and_stores_commands(self):
        sender = module.GCodeSender.__new__(module.GCodeSender)
        sender.command_history = []
        sender._history_index = 0
        sender._history_edit_buffer = ""
        sender.cmd_var = DummyVar()

        sender._add_to_history("G1 X10")
        sender._add_to_history("M104 S200")

        sender._history_index = len(sender.command_history)
        sender._navigate_history(-1)
        self.assertEqual(sender.cmd_var.get(), "M104 S200")

        sender._navigate_history(-1)
        self.assertEqual(sender.cmd_var.get(), "G1 X10")

        sender._navigate_history(-1)
        self.assertEqual(sender.cmd_var.get(), "G1 X10")

        sender._navigate_history(1)
        self.assertEqual(sender.cmd_var.get(), "M104 S200")


if __name__ == "__main__":
    unittest.main()
