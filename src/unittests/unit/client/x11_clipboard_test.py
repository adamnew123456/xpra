#!/usr/bin/env python
# This file is part of Xpra.
# Copyright (C) 2016 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import sys
import time
import unittest
from xpra.util import envbool
from xpra.os_util import get_hex_uuid
from unit.client.x11_client_test_util import X11ClientTestUtil

from xpra.log import Logger
log = Logger("clipboard")


SANITY_CHECKS = envbool("XPRA_CLIPBOARD_SANITY_CHECKS", True)


class X11ClipboardTest(X11ClientTestUtil):

	@classmethod
	def setUpClass(cls):
		super(X11ClipboardTest, cls).setUpClass()
		X11ClipboardTest.default_xpra_args += ["-d clipboard"]


	def get_clipboard_value(self, display, selection="clipboard"):
		return self.get_command_output("xclip -d %s -selection %s -o" % (display, selection), shell=True)

	def set_clipboard_value(self, display, value, selection="clipboard"):
		xclip = self.run_command("echo -n '%s' | xclip -d %s -selection %s -i" % (value, display, selection), shell=True)
		assert self.pollwait(xclip, 5)==0, "xclip returned %s" % xclip.poll()


	def copy_and_verify(self, display1, display2, synced=True, wait=1):
		log("copy_and_verify%s", (display1, display2, synced, wait))
		value = get_hex_uuid()
		self.set_clipboard_value(display1, value)
		#wait for synchronization to occur:
		time.sleep(wait)
		new_value = self.get_clipboard_value(display2)
		if synced:
			assert new_value==value, "clipboard contents do not match, expected '%s' but got '%s'" % (value, new_value)
		else:
			assert new_value!=value, "clipboard contents match but synchronization was not expected: value='%s'" % value
		if SANITY_CHECKS and display2!=display1:
			#verify that the value has not changed on the original display:
			new_value = self.get_clipboard_value(display1)
		return value

	def do_test_copy(self, direction="both"):
		server = self.run_server()
		server_display = server.display
		#connect a client:
		xvfb, client = self.run_client(server_display, "--clipboard-direction=%s" % direction)
		assert self.pollwait(client, 2) is None, "client has exited with return code %s" % client.poll()
		client_display = xvfb.display

		if SANITY_CHECKS:
			#xclip sanity check: retrieve from the same display:
			self.copy_and_verify(client_display, client_display, True, wait=0)
			self.copy_and_verify(server_display, server_display, True, wait=0)

		for _ in range(2):
			self.copy_and_verify(client_display, server_display, direction in ("both", "to-server"))
		for _ in range(2):
			self.copy_and_verify(server_display, client_display, direction in ("both", "to-client"))
		for _ in range(2):
			self.copy_and_verify(client_display, server_display, direction in ("both", "to-server"))

		client.terminate()
		xvfb.terminate()
		server.terminate()

	def test_copy(self):
		self.do_test_copy()

	def test_disabled(self):
		self.do_test_copy("disabled")

	def test_to_server(self):
		self.do_test_copy("to-server")

	def test_to_client(self):
		self.do_test_copy("to-client")


def main():
	if os.name=="posix" and sys.version_info[0]==2:
		unittest.main()


if __name__ == '__main__':
	main()
