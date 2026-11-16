"""Profile repeatable history workflows without a display or Ollama server."""

import argparse
import cProfile
from pathlib import Path
import pstats
import tempfile

from chat_store import ChatStore
from workspace_ui import _history_matches


def history_workflows(chats, messages):
    with tempfile.TemporaryDirectory(prefix="aiassist-profile-") as folder:
        store = ChatStore(folder)
        for index in range(chats):
            record = store.new_record(model="profile:8b", title=f"Conversation {index}")
            record["messages"] = [dict(role="user" if number % 2 == 0 else "assistant",
                content=f"Message {number}: " + "Example source and explanation. " * 40)
                for number in range(messages)]
            store.save(record)
        records = store.list_chats()
        for query in ("conversation", "message", "absent", "explanation"):
            for record in records:
                _history_matches(record, query)
        for record in records:
            opened = store.load(record["id"])
            opened["draft"] = "A follow-up draft"
            store.save(opened)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chats", type=int, default=20)
    parser.add_argument("--messages", type=int, default=200)
    parser.add_argument("--output", type=Path, help="Optional cProfile data file")
    args = parser.parse_args()
    if not 1 <= args.chats <= 1000 or not 1 <= args.messages <= 10000:
        parser.error("Use 1–1000 chats and 1–10000 messages per chat.")
    profiler = cProfile.Profile()
    profiler.runcall(history_workflows, args.chats, args.messages)
    pstats.Stats(profiler).strip_dirs().sort_stats("cumulative").print_stats(20)
    if args.output:
        profiler.dump_stats(str(args.output))


if __name__ == "__main__":
    main()
