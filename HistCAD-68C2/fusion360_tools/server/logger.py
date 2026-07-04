"""Logger utility class to output log info to the Fusion TextCommands window"""

import os
import time

import adsk.core
import adsk.fusion


class Logger:
    def __init__(self):
        self._stdout_enabled = self._env_flag(
            "FUSION360_SERVER_STDOUT_LOG", default=True
        )
        self._ui_enabled = self._env_flag("FUSION360_SERVER_UI_LOG", default=True)
        self.text_palette = None

        if self._ui_enabled:
            app = adsk.core.Application.get()
            ui = app.userInterface
            self.text_palette = ui.palettes.itemById("TextCommands")

            if self.text_palette is not None and not self.text_palette.isVisible:
                self.text_palette.isVisible = True

    @staticmethod
    def _env_flag(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return raw.strip().lower() not in {"", "0", "false", "no", "off"}

    def log(self, txt_str=""):
        if self._stdout_enabled:
            print(txt_str)
        if self._ui_enabled and self.text_palette is not None:
            self.text_palette.writeText(txt_str)
            adsk.doEvents()

    def log_time(self, txt_str=""):
        time_stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        time_txt_str = f"{time_stamp} {txt_str}"
        if self._stdout_enabled:
            print(time_txt_str)
        if self._ui_enabled and self.text_palette is not None:
            self.text_palette.writeText(time_txt_str)
            adsk.doEvents()
