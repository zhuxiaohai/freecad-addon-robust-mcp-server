"""Launcher for multiple Fusion 360 server instances."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..client.fusion360_client import Fusion360Client

SERVER_DIR = Path(__file__).resolve().parent
LAUNCH_JSON_FILE = Path(tempfile.gettempdir()) / "export_fusion360_launch.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
FUSION_LAUNCHER_ENV_NAMES = (
    "FUSION360_LAUNCHER",
    "FUSION360_PATH",
    "FUSION360_EXECUTABLE",
)


def get_launch_json_file() -> Path:
    """Return the launch.json path, optionally overridden per worker."""
    env_path = os.environ.get("FUSION360_LAUNCH_JSON")
    if env_path:
        return Path(env_path).expanduser()
    return LAUNCH_JSON_FILE


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--detach",
        dest="detach",
        default=False,
        action="store_true",
        help="Detach the launched Fusion 360 instances [default: False]",
    )
    parser.add_argument(
        "--ping",
        dest="ping",
        default=False,
        action="store_true",
        help="Ping the launched Fusion 360 instances [default: False]",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=DEFAULT_HOST,
        help="Host name as an IP address [default: 127.0.0.1]",
    )
    parser.add_argument(
        "--start_port",
        type=int,
        default=DEFAULT_PORT,
        help="The starting port for the first Fusion 360 instance [default: 8080]",
    )
    parser.add_argument(
        "--instances",
        type=int,
        default=1,
        help="The number of Fusion 360 instances to start [default: 1]",
    )
    return parser


class Launcher:
    def __init__(self) -> None:
        self.linux_launcher = self.find_linux_launcher()
        self.fusion_launcher = self.find_fusion_launcher_exe()
        self.fusion_app = self.find_fusion()
        launch_target = self._launch_target()
        if launch_target is None:
            print("Error: Fusion 360 could not be found")
        elif not launch_target.exists():
            print(f"Error: Fusion 360 does not exist at {launch_target}")
        else:
            print(f"Fusion 360 found at {launch_target}")

    def _launch_target(self) -> Path | None:
        if sys.platform.startswith("linux"):
            return self.linux_launcher
        if sys.platform == "win32":
            return self.fusion_launcher or self.fusion_app
        return self.fusion_app

    def _env_launcher_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        for name in FUSION_LAUNCHER_ENV_NAMES:
            env_launcher = os.environ.get(name)
            if env_launcher:
                candidates.append(Path(env_launcher).expanduser())
        return candidates

    def launch(self):
        """Open a new instance of Fusion 360."""
        launch_target = self._launch_target()
        if launch_target is None:
            print("Error: Fusion 360 could not be found")
            return None
        if not launch_target.exists():
            print(f"Error: Fusion 360 does not exist at {launch_target}")
            return None

        fusion_path = str(launch_target.resolve())
        args: list[str]
        cwd = None
        if sys.platform == "darwin":
            args = ["open", "-W", "-n", fusion_path]
        elif sys.platform == "win32":
            args = [fusion_path]
            cwd = str(launch_target.parent)
        else:
            if os.access(fusion_path, os.X_OK):
                args = [fusion_path]
            else:
                args = ["/bin/bash", fusion_path]
            cwd = str(launch_target.parent)

        print(f"Fusion launching from {fusion_path}")
        return subprocess.Popen(
            args,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def find_fusion(self) -> Path | None:
        """Find the Fusion application bundle or executable."""
        if sys.platform == "darwin":
            return self.find_fusion_mac()
        if sys.platform == "win32":
            return self.find_fusion_windows()
        return None

    def find_linux_launcher(self) -> Path | None:
        """Find a Linux/Wine launcher wrapper when Fusion runs through Wine."""
        if not sys.platform.startswith("linux"):
            return None

        candidates = self._env_launcher_candidates()
        candidates.append(Path.home() / ".local/bin/autodesk_fusion_launcher.sh")
        candidates.append(Path.home() / ".local/bin/fusion360_launcher.sh")

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def find_fusion_mac(self) -> Path:
        """Find the Fusion app on macOS."""
        user_path = Path.home()
        return (
            user_path
            / "Library/Application Support/Autodesk/webdeploy/production/Autodesk Fusion 360.app"
        )

    def find_fusion_windows(self) -> Path | None:
        """Find Fusion360.exe by inspecting FusionLauncher.exe.ini."""
        for candidate in self._env_launcher_candidates():
            if candidate.name.lower() == "fusion360.exe" and candidate.exists():
                return candidate
        fusion_launcher = self.find_fusion_launcher()
        if fusion_launcher is None:
            return None
        with open(fusion_launcher, encoding="utf16") as handle:
            lines = [line.strip() for line in handle.readlines()]

        for line in lines:
            if line.startswith("cmd") and "Fusion360.exe" in line:
                for piece in line.split('"'):
                    if "Fusion360.exe" in piece:
                        return Path(piece)
        return None

    def find_fusion_launcher(self) -> Path | None:
        """Find the FusionLauncher.exe.ini file on Windows."""
        if sys.platform != "win32":
            return None

        local_appdata = os.environ.get("LOCALAPPDATA")
        if not local_appdata:
            return None

        production_dir = Path(local_appdata) / "Autodesk/webdeploy/production"
        if not production_dir.exists():
            return None

        for item in production_dir.iterdir():
            if not item.is_dir():
                continue
            fusion_launcher = item / "FusionLauncher.exe.ini"
            if fusion_launcher.exists():
                return fusion_launcher
        return None

    def find_fusion_launcher_exe(self) -> Path | None:
        """Find the FusionLauncher executable on Windows."""
        if sys.platform == "win32":
            for candidate in self._env_launcher_candidates():
                if (
                    candidate.name.lower() == "fusionlauncher.exe"
                    and candidate.exists()
                ):
                    return candidate
        fusion_launcher_ini = self.find_fusion_launcher()
        if fusion_launcher_ini is None:
            return None
        fusion_launcher_exe = fusion_launcher_ini.parent / "FusionLauncher.exe"
        if fusion_launcher_exe.exists():
            return fusion_launcher_exe
        return None


def create_launch_json(host, start_port, instances):
    """Launch instruction file to be read by the server on startup."""
    launch_data = {}
    for instance in range(instances):
        port = start_port + instance
        url = f"http://{host}:{port}"
        launch_data[url] = {
            "host": host,
            "port": port,
            "connected": False,
        }
    launch_json_file = get_launch_json_file()
    launch_json_file.parent.mkdir(parents=True, exist_ok=True)
    os.environ["FUSION360_LAUNCH_JSON"] = str(launch_json_file)
    with open(launch_json_file, "w", encoding="utf-8") as file_handle:
        json.dump(launch_data, file_handle, indent=4)


def launch_instances(host, start_port, instances):
    """Launch multiple instances of Fusion 360."""
    launcher = Launcher()
    for port in range(start_port, start_port + instances):
        print(f"Launching Fusion 360 instance: {host}:{port}")
        launcher.launch()
        time.sleep(5)


def detach_endpoint(endpoint):
    """Detach an endpoint."""
    try:
        client = Fusion360Client(endpoint)
        print(f"Detaching {endpoint}...")
        client.detach()
    except Exception as ex:
        print(f"Error detaching server {endpoint}: {ex}")


def detach(host: str = DEFAULT_HOST, start_port: int = DEFAULT_PORT):
    """Detach the launched servers to make the Fusion UI responsive."""
    launch_json_file = get_launch_json_file()
    if launch_json_file.exists():
        with open(launch_json_file, encoding="utf-8") as file_handle:
            launch_data = json.load(file_handle)
            for endpoint, server in launch_data.items():
                if server["connected"]:
                    detach_endpoint(endpoint)
    else:
        detach_endpoint(f"http://{host}:{start_port}")


def ping_endpoint(endpoint):
    """Ping an endpoint."""
    try:
        client = Fusion360Client(endpoint)
        response = client.ping()
        if isinstance(response, dict):
            status = response.get("status", "unknown")
        else:
            status = getattr(response, "status_code", response)
        print(f"Ping response from {endpoint}: {status}")
    except Exception as ex:
        print(f"Error pinging server {endpoint}: {ex}")


def ping(host: str = DEFAULT_HOST, start_port: int = DEFAULT_PORT):
    """Ping the launched servers to see if they respond."""
    launch_json_file = get_launch_json_file()
    if launch_json_file.exists():
        with open(launch_json_file, encoding="utf-8") as file_handle:
            launch_data = json.load(file_handle)
            for endpoint, server in launch_data.items():
                ping_endpoint(endpoint)
    else:
        ping_endpoint(f"http://{host}:{start_port}")


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.ping:
        ping(args.host, args.start_port)
    elif args.detach:
        detach(args.host, args.start_port)
    else:
        create_launch_json(args.host, args.start_port, args.instances)
        launch_instances(args.host, args.start_port, args.instances)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
