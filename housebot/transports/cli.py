"""Interactive Terminal (CLI) transport for HouseBot.

Allows testing, chatting, and managing reminders/notes directly from
the terminal without requiring any external messaging app or network daemon.
"""

import sys


class CliTransport:
    push = False

    def __init__(self, cfg, engine):
        self.cfg = cfg
        self.engine = engine
        self.sender = "cli_user"

    def send(self, recipient, text, attachment=None):
        print(f"\n[Push -> {recipient}]: {text}")

    def serve_forever(self):
        print("\n" + "=" * 55)
        print("  HouseBot Interactive Terminal Mode")
        print("  Type your message (or 'exit' / 'quit' to stop)")
        print("=" * 55 + "\n")

        while True:
            try:
                text = input("You > ").strip()
                if not text:
                    continue
                if text.lower() in ("exit", "quit", "bye"):
                    print("Goodbye!")
                    break
                reply, _attachment = self.engine.handle(self.sender, text)
                if reply:
                    print(f"\nHouseBot > {reply}\n")
            except (KeyboardInterrupt, EOFError):
                print("\nGoodbye!")
                break
            except Exception as e:
                print(f"\nError: {e}\n")
