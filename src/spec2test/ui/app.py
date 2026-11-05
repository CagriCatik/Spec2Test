"""Application entry point for the graphical interface."""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Start the window. ``--config PATH`` overrides the configuration file."""
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    argv = list(sys.argv if argv is None else [sys.argv[0] if sys.argv else "spec2test-gui", *argv])
    config_path: Path | None = None
    if "--config" in argv:
        index = argv.index("--config")
        if index + 1 < len(argv):
            config_path = Path(argv[index + 1])
            del argv[index : index + 2]

    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("Spec2Test")
    window = MainWindow(config_path=config_path)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
