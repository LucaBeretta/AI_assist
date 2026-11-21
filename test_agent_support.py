"""Keep default agent storage out of both user data and temporary projects."""

from pathlib import Path
import tempfile
from unittest.mock import patch


def isolate_agent_data(case):
    directory = tempfile.TemporaryDirectory()
    case.addCleanup(directory.cleanup)
    # The real path: macOS temporary folders sit behind the /var link and Windows can report short (8.3) names.
    path = Path(directory.name).resolve()
    override = patch.dict("os.environ", {"AI_ASSIST_DATA_DIR": str(path)})
    override.start()
    case.addCleanup(override.stop)
    return path
