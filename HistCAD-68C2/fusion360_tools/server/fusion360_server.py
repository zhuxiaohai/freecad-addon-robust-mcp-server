import json
import os
import shutil
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import adsk.core
import adsk.fusion

from .command_runner import CommandRunner
from .logger import Logger

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
START_SERVER_CUSTOM_EVENT_ID = "fusion360_server_start_http"

# Event handlers
handlers = []
server_running = False
server_start_requested = False
startup_poll_thread = None


def _is_client_disconnect_error(error: Exception) -> bool:
    return isinstance(
        error,
        (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError),
    )


def _bootstrap_log_path() -> Path:
    return Path(tempfile.gettempdir()) / "export_fusion_server.log"


def _bootstrap_log(message: str) -> None:
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(_bootstrap_log_path(), "a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")
    except Exception:
        pass


def _launch_json_path() -> Path:
    env_path = os.environ.get("FUSION360_LAUNCH_JSON")
    if env_path:
        return Path(env_path).expanduser()
    return Path(tempfile.gettempdir()) / "export_fusion360_launch.json"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _verbose_request_logging_enabled() -> bool:
    return _env_flag("FUSION360_SERVER_VERBOSE_REQUEST_LOG", default=False)


def _set_launch_endpoint_connected(
    host_name: str, port_number: int, connected: bool
) -> None:
    launch_json_file = _launch_json_path()
    if not launch_json_file.exists():
        return

    with open(launch_json_file, encoding="utf-8") as file_handle:
        launch_data = json.load(file_handle)

    updated = False
    for server in launch_data.values():
        if server.get("host") == host_name and server.get("port") == port_number:
            if server.get("connected") != connected:
                server["connected"] = connected
                updated = True
            break

    if not updated:
        return

    with open(launch_json_file, "w", encoding="utf-8") as file_handle:
        json.dump(launch_data, file_handle, indent=4)


class StartupCompletedHandler(adsk.core.ApplicationEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        _bootstrap_log("startupCompleted event fired")
        print("Fusion startupCompleted fired; starting server")
        start_server()


class DeferredStartServerHandler(adsk.core.CustomEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        _bootstrap_log("deferred custom start event fired")
        print("Fusion deferred start event fired; starting server")
        start_server()


def _ensure_deferred_start_handler(app: adsk.core.Application) -> None:
    if any(isinstance(handler, DeferredStartServerHandler) for handler in handlers):
        return
    try:
        custom_event = app.registerCustomEvent(START_SERVER_CUSTOM_EVENT_ID)
    except Exception:
        custom_event = app.customEvents.itemById(START_SERVER_CUSTOM_EVENT_ID)
    if custom_event is None:
        raise RuntimeError(
            f"Failed to register custom event: {START_SERVER_CUSTOM_EVENT_ID}"
        )
    deferred_handler = DeferredStartServerHandler()
    custom_event.add(deferred_handler)
    handlers.append(custom_event)
    handlers.append(deferred_handler)


def _request_server_start(app: adsk.core.Application, source: str) -> None:
    global server_start_requested
    if server_running or server_start_requested:
        _bootstrap_log(
            f"skip deferred start request source={source} server_running={server_running} requested={server_start_requested}"
        )
        return
    server_start_requested = True
    _bootstrap_log(f"requesting deferred server start via {source}")
    print(f"Fusion requesting deferred server start via {source}")
    app.fireCustomEvent(START_SERVER_CUSTOM_EVENT_ID)


def _start_startup_poll(app: adsk.core.Application) -> None:
    global startup_poll_thread
    if startup_poll_thread is not None and startup_poll_thread.is_alive():
        return

    def _poll_startup_complete():
        try:
            while not server_running:
                if app.isStartupComplete:
                    _request_server_start(app, "startup-poll")
                    return
                time.sleep(0.5)
        except Exception:
            _bootstrap_log("startup poll thread exception")
            _bootstrap_log(traceback.format_exc())
            print(traceback.format_exc())

    startup_poll_thread = threading.Thread(
        target=_poll_startup_complete,
        name="fusion360-server-startup-poll",
        daemon=True,
    )
    startup_poll_thread.start()


class Fusion360GymServerRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, logger, runner, *args):
        self.logger = logger
        self.runner = runner
        BaseHTTPRequestHandler.__init__(self, *args)

    def do_HEAD(self):
        return

    def do_POST(self):
        command = "<unknown>"
        close_after_response = False
        try:
            post_data = self.get_post_data()
            verbose_request_log = _verbose_request_logging_enabled()
            if verbose_request_log:
                self.logger.log("\n")
            if "command" not in post_data:
                self.respond(400, "Command not present")
                return

            app = adsk.core.Application.get()
            if not app.isStartupComplete:
                self.respond(500, "Start-up is not completed")
                return

            command = post_data["command"]
            if verbose_request_log:
                self.logger.log(f"Command: {command}")
            if command == "detach":
                close_after_response = True
                if verbose_request_log:
                    self.logger.log("Shutting down...")
                self.detach()

            data = None
            if "data" in post_data:
                data = post_data["data"]

            status_code, message, return_data = self.runner.run_command(command, data)
            if return_data is not None and isinstance(return_data, Path):
                if verbose_request_log:
                    self.logger.log(f"[{status_code}] {return_data}")
                self.respond_binary_file(status_code, return_data)
            else:
                if verbose_request_log:
                    self.logger.log(f"[{status_code}] {message}")
                self.respond(
                    status_code,
                    message,
                    return_data,
                    close_connection=close_after_response,
                )

        except Exception as ex:
            message = f"""Error processing {command} command\n
                Exception of type {type(ex)} with args: {ex.args}\n
                {traceback.format_exc()}"""
            self.logger.log(message)
            try:
                self.respond(500, ex, close_connection=close_after_response)
            except Exception as respond_ex:
                if _is_client_disconnect_error(respond_ex):
                    self.logger.log(
                        f"client disconnected while sending error response for {command}: {respond_ex}"
                    )
                    self.close_connection = True
                    return
                raise

    def do_GET(self):
        self.respond(400, "GET not supported, use POST")

    def get_post_data(self):
        content_len = int(self.headers.get("Content-Length"))
        post_body = self.rfile.read(content_len)
        # self.logger.log(f"post_body: {post_body}")
        post_body_json = json.loads(post_body)
        return post_body_json

    def respond_binary_file(self, status_code, binary_file):
        file_size = binary_file.stat().st_size
        try:
            self.send_response(status_code)
            self.send_header("Content-type", "application/octet-stream")
            self.send_header("Content-Length", str(file_size))
            self.send_header("Connection", "close")
            self.end_headers()
            with open(binary_file, "rb") as file_handle:
                shutil.copyfileobj(file_handle, self.wfile)
        except Exception as ex:
            if _is_client_disconnect_error(ex):
                self.logger.log(
                    f"client disconnected while streaming binary response: {ex}"
                )
                self.close_connection = True
                return
            raise
        finally:
            binary_file.unlink(missing_ok=True)
            self.close_connection = True

    def respond(self, status_code, message, return_data=None, close_connection=True):
        data = {"status": status_code, "message": message}
        if return_data is not None:
            data["data"] = return_data
        response_bytes = json.dumps(data).encode(encoding="utf_8")
        try:
            self.send_response(status_code)
            self.send_header("Content-type", "application/json")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response_bytes)
        except Exception as ex:
            if _is_client_disconnect_error(ex):
                self.logger.log(f"client disconnected while sending response: {ex}")
                self.close_connection = True
                return
            raise
        self.close_connection = True

    def detach(self):
        host_name, port_number = self.server.server_address[:2]
        _set_launch_endpoint_connected(host_name, port_number, False)
        self.close_connection = True
        # We have to shutdown the server from a separate thread to avoid deadlock
        server_shutdown_thread = threading.Thread(target=self.server.shutdown)
        server_shutdown_thread.daemon = True
        server_shutdown_thread.start()


def get_launch_endpoint():
    """If we are launching multiple instances, find the right endpoint to use"""
    # Default endpoint
    host_name = DEFAULT_HOST
    port_number = DEFAULT_PORT
    # If we have a launch file then we find the host and port to use
    launch_json_file = _launch_json_path()
    if launch_json_file.exists():
        with open(launch_json_file) as file_handle:
            launch_data = json.load(file_handle)
            for endpoint, server in launch_data.items():
                # If this server isn't connected, lets grab it and use it
                if not server["connected"]:
                    host_name = server["host"]
                    port_number = server["port"]
                    # Claim the server
                    server["connected"] = True
                    break
        # Write out the claimed endpoint so sequential launches get distinct ports.
        with open(launch_json_file, "w") as f:
            json.dump(launch_data, f, indent=4)
    return host_name, port_number


class FusionHTTPServer(HTTPServer):
    def handle_error(self, request, client_address):
        _bootstrap_log(f"request handler error from {client_address}")
        _bootstrap_log(traceback.format_exc())
        try:
            super().handle_error(request, client_address)
        except Exception:
            pass


def start_server():
    """Start the server"""
    global server_running
    global server_start_requested
    if server_running:
        _bootstrap_log("start_server skipped because server_running is already true")
        print("Fusion 360 server is already running; skipping duplicate start request")
        return
    server_running = True
    server_start_requested = False
    _bootstrap_log("start_server entered")

    # # Setup the logger globally after Fusion has started
    logger = Logger()
    logger.log("Started server...")
    # # Set up the command runner we use to execute commands
    runner = CommandRunner()
    runner.set_logger(logger)

    # Workaround to pass the logger and runner
    def handler(*args):
        Fusion360GymServerRequestHandler(logger, runner, *args)

    # Check if we need to use a different host name and port
    host_name, port_number = get_launch_endpoint()
    _bootstrap_log(f"start_server resolved endpoint {host_name}:{port_number}")

    # Launch the server which will block the UI thread
    logger.log(f"Connecting on: {host_name}:{port_number}")
    try:
        server = FusionHTTPServer((host_name, port_number), handler)
    except Exception:
        _bootstrap_log("HTTPServer bind failed")
        _bootstrap_log(traceback.format_exc())
        server_running = False
        server_start_requested = False
        raise
    _bootstrap_log(f"HTTPServer bound on {host_name}:{port_number}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    except Exception as ex:
        _bootstrap_log(f"server loop exception: {ex}")
        _bootstrap_log(traceback.format_exc())
        logger.log(str(ex))
    finally:
        server.server_close()
        server_running = False
        server_start_requested = False
        _bootstrap_log("server loop exited")


def run(context):
    try:
        app = adsk.core.Application.get()
        _bootstrap_log(
            f"run entered isStartupComplete={getattr(app, 'isStartupComplete', None)}"
        )
        _ensure_deferred_start_handler(app)
        # If we have started the server manually
        # we go ahead and startup
        if app.isStartupComplete:
            _bootstrap_log("run starting server immediately")
            print("Fusion startup already complete; starting server immediately")
            start_server()
        else:
            # When the add-in starts during Fusion launch, wait for the
            # startupCompleted event before binding the HTTP server.
            _bootstrap_log("run deferring server start until startup completes")
            print("Fusion startup not complete; waiting for startupCompleted event")
            on_startup_completed = StartupCompletedHandler()
            app.startupCompleted.add(on_startup_completed)
            handlers.append(on_startup_completed)
            _start_startup_poll(app)
    except Exception:
        _bootstrap_log("run exception")
        _bootstrap_log(traceback.format_exc())
        print(traceback.format_exc())
