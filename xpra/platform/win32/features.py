# This file is part of Xpra.
# Copyright (C) 2010 Nathaniel Smith <njs@pobox.com>
# Copyright (C) 2011 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from collections.abc import Sequence

# Platform-specific settings for Win32.
CAN_DAEMONIZE = False
REINIT_WINDOWS = True

CLIPBOARDS = ("CLIPBOARD",)
CLIPBOARD_GREEDY = True

SOURCE: Sequence[str] = ()

EXECUTABLE_EXTENSION = "exe"

# these don't make sense on win32:
DEFAULT_PULSEAUDIO_CONFIGURE_COMMANDS = ()
PRINT_COMMAND = ""
DEFAULT_SSH_COMMAND = "plink.exe -ssh -agent"

OPEN_COMMAND = ["start", "''"]

# not implemented:
SYSTEM_PROXY_SOCKET = "xpra-proxy"

SOCKET_OPTIONS = (
    # not supported on win32:
    # "SO_BROADCAST", "SO_RCVLOWAT",
    "SO_DONTROUTE", "SO_ERROR", "SO_EXCLUSIVEADDRUSE",
    "SO_KEEPALIVE", "SO_LINGER", "SO_OOBINLINE", "SO_RCVBUF",
    "SO_RCVTIMEO", "SO_REUSEADDR", "SO_REUSEPORT",
    "SO_SNDBUF", "SO_SNDTIMEO", "SO_TIMEOUT", "SO_TYPE",
)
