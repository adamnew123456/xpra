# This file is part of Xpra.
# Copyright (C) 2022-2024 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.
# pylint: disable-msg=E1101

import os
import sys
from typing import Any

from xpra.util.version import version_str
from xpra.util.objects import typedict
from xpra.util.str_fn import csv
from xpra.util.env import envint, envfloat
from xpra.common import ConnectionMessage
from xpra.os_util import get_machine_id, gi_import, POSIX, OSX
from xpra.net.bytestreams import log_new_connection
from xpra.net.socket_util import create_sockets, add_listen_socket, accept_connection, setup_local_sockets
from xpra.net.net_util import get_network_caps
from xpra.net.common import is_request_allowed
from xpra.net.protocol.socket_handler import SocketProtocol
from xpra.net.protocol.constants import CONNECTION_LOST, GIBBERISH
from xpra.exit_codes import ExitCode
from xpra.client.base.stub_client_mixin import StubClientMixin
from xpra.scripts.config import InitException, InitExit
from xpra.log import Logger

log = Logger("network")

SOCKET_TIMEOUT = envfloat("XPRA_CLIENT_SOCKET_TIMEOUT", 0.1)
MAX_CONCURRENT_CONNECTIONS = envint("XPRA_MAX_CONCURRENT_CONNECTIONS", 5)
REQUEST_TIMEOUT = envint("XPRA_CLIENT_REQUEST_TIMEOUT", 10)


class Networklistener(StubClientMixin):
    """
    Mixin for adding listening sockets to the client,
    those can be used for
    - requesting disconnection
    - info request
    """

    def __init__(self):
        super().__init__()
        self.sockets = {}
        self.socket_info = {}
        self.socket_options = {}
        self.socket_cleanup = []
        self._potential_protocols = []
        self._close_timers = {}

    def init(self, opts) -> None:
        def err(msg):
            raise InitException(msg)

        self.sockets = create_sockets(opts, err)
        log(f"setup_local_sockets bind={opts.bind}, client_socket_dirs={opts.client_socket_dirs}")
        try:
            # don't use abstract sockets for clients:
            # (as these may collide with display numbers)
            bind = ["noabstract"] if csv(opts.bind) == "auto" else opts.bind
            local_sockets = setup_local_sockets(bind,
                                                "", opts.client_socket_dirs, "",
                                                str(os.getpid()), True,
                                                opts.mmap_group, opts.socket_permissions)
        except (OSError, InitExit, ImportError) as e:
            log("setup_local_sockets bind=%s, client_socket_dirs=%s",
                opts.bind, opts.client_socket_dirs, exc_info=True)
            log.warn("Warning: failed to create the client sockets:")
            log.warn(" '%s'", e)
        else:
            self.sockets.update(local_sockets)

    def run(self) -> None:
        self.start_listen_sockets()

    def cleanup(self) -> None:
        self.cleanup_sockets()

    def cleanup_sockets(self) -> None:
        ct = dict(self._close_timers)
        self._close_timers = {}
        for proto, tid in ct.items():
            self.source_remove(tid)
            proto.close()
        socket_cleanup = self.socket_cleanup
        log("cleanup_sockets() socket_cleanup=%s", socket_cleanup)
        self.socket_cleanup = []
        for c in socket_cleanup:
            with log.trap_error("Error during socket listener cleanup %s", c):
                c()
        sockets = self.sockets
        self.sockets = {}
        log("cleanup_sockets() sockets=%s", sockets)
        for sdef in sockets.keys():
            c = sdef[-1]
            with log.trap_error("Error during socket cleanup %s", c):
                c()

    def start_listen_sockets(self) -> None:
        for sock_def, options in self.sockets.items():
            socktype, sock, info, _ = sock_def
            log("start_listen_sockets() will add %s socket %s (%s)", socktype, sock, info)
            self.socket_info[sock] = info
            self.socket_options[sock] = options
            self.idle_add(self.add_listen_socket, socktype, sock, options)

    def add_listen_socket(self, socktype: str, sock, options) -> None:
        info = self.socket_info.get(sock)
        log("add_listen_socket(%s, %s, %s) info=%s", socktype, sock, options, info)
        cleanup = add_listen_socket(socktype, sock, info, None, self._new_connection, options)
        if cleanup:
            self.socket_cleanup.append(cleanup)

    def _new_connection(self, socktype: str, listener, handle: int = 0) -> bool:
        """
            Accept the new connection,
            verify that there aren't too many,
            start a thread to dispatch it to the correct handler.
        """
        log("_new_connection%s", (listener, socktype, handle))
        if self.exit_code is not None:
            log("ignoring new connection during shutdown")
            return False
        with log.trap_error(f"Error handling new {socktype} connection"):
            self.handle_new_connection(socktype, listener, handle)
        return self.exit_code is None

    def handle_new_connection(self, socktype, listener, handle) -> None:
        assert socktype, "cannot find socket type for %s" % listener
        socket_options = self.socket_options.get(listener, {})
        if socktype == "named-pipe":
            from xpra.platform.win32.namedpipes.connection import NamedPipeConnection
            conn = NamedPipeConnection(listener.pipe_name, handle, socket_options)
            log.info("New %s connection received on %s", socktype, conn.target)
            self.make_protocol(socktype, conn, listener)
            return
        conn = accept_connection(socktype, listener, SOCKET_TIMEOUT, socket_options)
        if conn is None:
            return
        # limit number of concurrent network connections:
        if len(self._potential_protocols) >= MAX_CONCURRENT_CONNECTIONS:
            log.error("Error: too many connections (%i)", len(self._potential_protocols))
            log.error(" ignoring new one: %s", conn.endpoint or conn)
            conn.close()
            return
        try:
            sockname = conn._socket.getsockname()
        except Exception:
            sockname = ""
        log("handle_new_connection%s sockname=%s", (socktype, listener, handle), sockname)
        socket_info = self.socket_info.get(listener)
        log_new_connection(conn, socket_info)
        self.make_protocol(socktype, conn, listener)

    def make_protocol(self, socktype, conn, listener) -> None:
        socktype = socktype.lower()
        protocol = SocketProtocol(self, conn, self.process_network_packet)
        # protocol.large_packets.append(b"info-response")
        protocol.socket_type = socktype
        self._potential_protocols.append(protocol)
        protocol.authenticators = ()
        protocol.start()
        # self.schedule_verify_connection_accepted(protocol, self._accept_timeout)

    def process_network_packet(self, proto, packet) -> None:
        log("process_network_packet: %s", packet)
        packet_type = str(packet[0])

        def close():
            t = self._close_timers.pop(proto, None)
            if t:
                proto.close()
            try:
                self._potential_protocols.remove(proto)
            except ValueError:
                pass

        if packet_type == "hello":
            caps = typedict(packet[1])
            proto.parse_remote_caps(caps)
            proto.enable_compressor_from_caps(caps)
            proto.enable_encoder_from_caps(caps)
            request = caps.strget("request")
            if request:
                self.handle_hello_request(proto, request, caps)
        elif packet_type in (CONNECTION_LOST, GIBBERISH):
            close()
            return
        else:
            log.info("packet '%s' is not handled by this client", packet_type)
            proto.send_disconnect([ConnectionMessage.PROTOCOL_ERROR])
        # make sure the connection is closed:
        tid = self.timeout_add(REQUEST_TIMEOUT * 1000, close)
        self._close_timers[proto] = tid

    def handle_hello_request(self, proto, request: str, caps: typedict) -> None:
        def hello_reply(data) -> None:
            proto.send_now(["hello", data])

        if not is_request_allowed(proto, request):
            log.info("request '%s' is not handled by this client", request)
            proto.send_disconnect([ConnectionMessage.PROTOCOL_ERROR])
            return

        if request == "info":
            def send_info() -> None:
                from xpra.platform.gui import get_session_type
                from xpra.util.system import platform_name
                info = self.get_info()
                info["network"] = get_network_caps()
                info["session-type"] = (get_session_type() or platform_name()) + " client"
                display = os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")
                if display and POSIX and not OSX:
                    info["display"] = display
                hello_reply(info)

            # run in UI thread:
            self.idle_add(send_info)
            return
        if request == "id":
            hello_reply(self.get_id_info())
            return
        if request == "detach":
            def protocol_closed() -> None:
                self.disconnect_and_quit(ExitCode.OK, "network request")

            proto.send_disconnect([ConnectionMessage.DETACH_REQUEST], done_callback=protocol_closed)
            return
        if request == "version":
            hello_reply({"version": version_str()})
            return
        if request in ("show-menu", "show-about", "show-session-info"):
            fn = getattr(self, request.replace("-", "_"), None)
            if not fn:
                hello_reply({"error": "%s not found" % request})
            else:
                log.info(f"calling {fn}")
                glib = gi_import("GLib")
                glib.idle_add(fn)
                hello_reply({})
            return
        if request == "connect_test":
            hello_reply({})
            return
        if request == "command":
            command = caps.strtupleget("command_request")
            log("command request: %s", command)

            def process_control():
                try:
                    self._process_control(["control"] + list(command))
                    code = ExitCode.OK
                    response = "done"
                except Exception as e:
                    code = ExitCode.FAILURE
                    response = str(e)
                hello_reply({"command_response": (int(code), response)})

            self.idle_add(process_control)
            return
        log.info(f"`{request}` requests are not handled by this client")
        proto.send_disconnect([ConnectionMessage.PROTOCOL_ERROR])

    def get_id_info(self) -> dict[str, Any]:
        # minimal information for identifying the session
        return {
            "session-type": "client",
            "session-name": self.session_name,
            "platform": sys.platform,
            "pid": os.getpid(),
            "machine-id": get_machine_id(),
        }
