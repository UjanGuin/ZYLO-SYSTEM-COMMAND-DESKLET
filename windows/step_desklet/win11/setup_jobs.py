"""Keep the app list build alive while the Windows tray app is running."""

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from step_desklet.win11.request_process import DeskletRequest


class AppListJobs(QObject):
    changed = pyqtSignal()

    def __init__(self, app: QApplication):
        super().__init__(app)
        self.request = None
        self.credentials = None
        self.draft = None
        self.status = ""
        app.aboutToQuit.connect(self.cancel)

    @classmethod
    def for_app(cls) -> "AppListJobs":
        app = QApplication.instance()
        jobs = getattr(app, "_zylo_app_list_jobs", None)
        if jobs is None:
            jobs = app._zylo_app_list_jobs = cls(app)
        return jobs

    def start(self, primary: str, backup: str, *, check_key: bool = False, draft=None) -> None:
        if self.request is not None and self.credentials == (primary, backup):
            if draft is None:
                self.draft = None
            return
        self.cancel()
        request = DeskletRequest(self)
        self.request = request
        self.credentials = (primary, backup)
        self.draft = draft
        request.status.connect(self._progress)
        request.outcome.connect(self._finished)
        self._progress("Building the app list in the background.")
        request.start_request({
            "operation": "rebuild_app_catalog",
            "groq_key": primary,
            "groq_backup_key": backup,
            "check_key": check_key,
        })

    def _progress(self, message: str) -> None:
        self.status = message
        self.changed.emit()

    def _finished(self, event: str, value: str) -> None:
        self.request = self.credentials = self.draft = None
        self._progress("" if event == "response" else f"Could not rebuild the app list. {value}")

    def cancel_draft(self, draft) -> None:
        if self.draft is draft:
            self.cancel()

    def cancel(self) -> None:
        if self.request is not None:
            self.request.cancel()
            self.request = self.credentials = self.draft = None
            self._progress("")
