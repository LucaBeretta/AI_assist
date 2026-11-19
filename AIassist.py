"""Start AI Assist's local desktop web interface."""
import argparse

from gui_runtime import redirect_missing_streams


def main(argv=None):
    redirect_missing_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", action="store_true", help="open the web UI (the default)")
    parser.parse_args(argv)
    from webui.app import main as run_web
    return run_web()


if __name__ == "__main__":
    raise SystemExit(main())
