"""Local-file HTML previews with one reusable window and owned temporary files."""

from pathlib import Path
import tempfile

import developer_tools


class HtmlPreview:
    def __init__(self, directory, desktop):
        self.directory = Path(directory).resolve()
        self.desktop = desktop
        self.temporary = None

    def file(self, project, path):
        root = developer_tools._project_root(project)
        target, _ = developer_tools._project_file(root, path)
        if not target.is_file() or target.suffix.lower() not in {".html", ".htm"}:
            raise ValueError("Choose a project HTML file to preview.")
        uri = target.as_uri()
        self.desktop.open_preview(uri)
        return uri

    def code(self, html):
        if not isinstance(html, str) or not html.strip() or len(html.encode("utf-8")) > 4000000:
            raise ValueError("Preview HTML must be nonempty text up to 4 MB.")
        fresh = self.temporary is None
        if fresh:
            self.temporary = tempfile.TemporaryDirectory(prefix=".html-preview-", dir=self.directory)
        folder = Path(self.temporary.name)
        target = folder / "index.html"
        if (folder.is_symlink() or getattr(folder, "is_junction", lambda: False)()
                or folder.resolve().parent != self.directory or target.is_symlink()
                or target.resolve().parent != folder.resolve()):
            raise ValueError("The preview file must stay in its temporary folder.")
        target.write_text(html, encoding="utf-8", newline="\n")
        try:
            self.desktop.open_preview(target.as_uri())
        except Exception:
            if fresh:
                self.temporary.cleanup()
                self.temporary = None
            raise
        return target.as_uri()

    def close(self):
        self.desktop.close()
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None
