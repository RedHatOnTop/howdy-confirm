"""Confirmation rubberstamp for howdy, with a prompt on the user's desktop.

Two things make this awkward, and both are handled here.

pam_howdy spawns the compare process with envp = NULL, so it starts with no
environment at all: no DISPLAY, no WAYLAND_DISPLAY, no XDG_RUNTIME_DIR. The
username is the one thing it is given, and the session can be found from that.

The compare process also inherits the setuid state of whatever is running the
pam stack. Under sudo that means ruid != euid, and GTK refuses to initialise in
a process like that. So the window is drawn by a helper: this same file, re-run
as the user being authenticated, which is both what GTK wants and less
privilege than drawing it as root.
"""

import glob
import math
import os
import pwd
import subprocess
import sys
import time

try:
	from i18n import _
except ImportError:
	# Running as the window helper, howdy's directory is not on the path yet
	sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

	try:
		from i18n import _
	except ImportError:
		# Untranslated beats no prompt at all
		def _(text):
			return text

try:
	# Import the root rubberstamp class
	from rubberstamps import RubberStamp
except ImportError:
	# Only the stamp needs the base class, the window helper never sees one
	RubberStamp = object


# Exit codes the window helper answers with, kept clear of python's own
EXIT_AUTHORISED = 10
EXIT_REFUSED = 11
EXIT_NO_WINDOW = 12

# Accent used for the ring, the glyph and the confirm button
ACCENT = (0.29, 0.64, 1.0)

STYLE = b"""
.howdy-root {
	background-color: transparent;
}
.howdy-card {
	background-image: linear-gradient(160deg, #262931, #15161b);
	border: 1px solid rgba(255, 255, 255, 0.09);
	border-radius: 20px;
	box-shadow: 0 26px 64px rgba(0, 0, 0, 0.62), 0 2px 10px rgba(0, 0, 0, 0.45);
}
.howdy-title {
	color: #f3f4f6;
	font-size: 19px;
	font-weight: bold;
}
.howdy-subtitle {
	color: rgba(243, 244, 246, 0.52);
	font-size: 12px;
}
.howdy-button {
	background-image: none;
	background-color: rgba(255, 255, 255, 0.06);
	color: #e9eaee;
	border: 1px solid rgba(255, 255, 255, 0.10);
	border-radius: 10px;
	padding: 9px 16px;
	text-shadow: none;
	box-shadow: none;
}
.howdy-button:hover {
	background-color: rgba(255, 255, 255, 0.12);
	border-color: rgba(255, 255, 255, 0.17);
}
.howdy-confirm {
	background-image: linear-gradient(180deg, #4aa3ff, #2b7fd4);
	color: #ffffff;
	border: 1px solid rgba(0, 0, 0, 0.30);
	box-shadow: 0 3px 12px rgba(42, 126, 212, 0.40);
}
.howdy-confirm:hover {
	background-image: linear-gradient(180deg, #5aadff, #3789dd);
}
"""


def find_session(username):
	"""Locate the display of a user's graphical session, or return None

	Returns the environment the window helper needs, since none of it is
	inherited from pam.
	"""
	try:
		entry = pwd.getpwnam(username)
	except KeyError:
		return None

	runtime_dir = "/run/user/" + str(entry.pw_uid)

	if not os.path.isdir(runtime_dir):
		return None

	env = {
		"XDG_RUNTIME_DIR": runtime_dir,
		"HOME": entry.pw_dir,
		"USER": username,
		"LOGNAME": username,
		"PATH": "/usr/bin:/bin",
	}

	# Prefer wayland, its socket lives in the runtime directory
	sockets = [s for s in sorted(glob.glob(runtime_dir + "/wayland-*")) if not s.endswith(".lock")]

	if sockets:
		env["WAYLAND_DISPLAY"] = os.path.basename(sockets[0])
		env["GDK_BACKEND"] = "wayland"
		return env

	# Otherwise take the lowest numbered X display
	x_sockets = sorted(glob.glob("/tmp/.X11-unix/X*"))

	if x_sockets:
		env["DISPLAY"] = ":" + os.path.basename(x_sockets[0])[1:]
		env["GDK_BACKEND"] = "x11"
		return env

	return None


def show_window(username, timeout, failsafe, abort_key):
	"""Draw the prompt and wait for an answer, as the user being authenticated"""
	import cairo
	import gi

	# Gdk has to be pinned as well, importing it unpinned picks up 4.0 and then
	# loading Gtk 3.0 fails on the version mismatch
	gi.require_version("Gtk", "3.0")
	gi.require_version("Gdk", "3.0")
	from gi.repository import Gdk, GLib, Gtk

	answer = {"value": None}
	deadline = time.time() + timeout
	remaining = {"fraction": 1.0}

	def draw_badge(area, ctx):
		"""Glow, draining countdown ring and face glyph, as one unit"""
		size = min(area.get_allocated_width(), area.get_allocated_height())
		cx = cy = size / 2
		ring = size / 2 - 4
		head = ring - 13

		# Soft glow so the badge does not sit flat on the card
		glow = cairo.RadialGradient(cx, cy, head * 0.3, cx, cy, ring * 1.05)
		glow.add_color_stop_rgba(0, *ACCENT, 0.20)
		glow.add_color_stop_rgba(1, *ACCENT, 0.0)
		ctx.set_source(glow)
		ctx.arc(cx, cy, ring * 1.05, 0, 2 * math.pi)
		ctx.fill()

		# Track the ring runs on
		ctx.set_line_width(3.0)
		ctx.set_source_rgba(1, 1, 1, 0.09)
		ctx.arc(cx, cy, ring, 0, 2 * math.pi)
		ctx.stroke()

		# The ring itself, draining clockwise from twelve o'clock
		if remaining["fraction"] > 0:
			ctx.set_line_cap(cairo.LINE_CAP_ROUND)
			ctx.set_source_rgba(*ACCENT, 0.95)
			ctx.arc(cx, cy, ring, -math.pi / 2,
			        -math.pi / 2 + 2 * math.pi * remaining["fraction"])
			ctx.stroke()
			ctx.set_line_cap(cairo.LINE_CAP_BUTT)

		# Head, left open under the chin so it reads as a scan outline
		ctx.set_line_width(2.0)
		ctx.set_source_rgba(*ACCENT, 1.0)
		ctx.arc(cx, cy, head, math.radians(115), math.radians(65) + 2 * math.pi)
		ctx.stroke()

		# Eyes
		for dx in (-head * 0.36, head * 0.36):
			ctx.arc(cx + dx, cy - head * 0.20, 2.2, 0, 2 * math.pi)
			ctx.fill()

		# Mouth
		ctx.arc(cx, cy + head * 0.02, head * 0.44, math.radians(28), math.radians(152))
		ctx.stroke()

	provider = Gtk.CssProvider()
	provider.load_from_data(STYLE)
	Gtk.StyleContext.add_provider_for_screen(
		Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

	window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
	window.set_decorated(False)
	window.set_resizable(False)
	window.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
	window.set_keep_above(True)
	window.set_type_hint(Gdk.WindowTypeHint.DIALOG)
	window.set_app_paintable(True)
	window.get_style_context().add_class("howdy-root")

	# Needed for the rounded corners and the drop shadow to composite
	visual = window.get_screen().get_rgba_visual()
	if visual:
		window.set_visual(visual)

	# The shadow is drawn outside the card, so leave it room
	frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
	frame.get_style_context().add_class("howdy-root")
	frame.set_border_width(28)
	window.add(frame)

	card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
	card.get_style_context().add_class("howdy-card")
	card.set_border_width(30)
	card.set_size_request(330, -1)
	frame.pack_start(card, True, True, 0)

	badge = Gtk.DrawingArea()
	badge.set_size_request(96, 96)
	badge.set_halign(Gtk.Align.CENTER)

	# Open space above the glyph, the card reads as cramped without it
	badge.set_margin_top(26)
	badge.connect("draw", draw_badge)
	card.pack_start(badge, False, False, 0)

	title = Gtk.Label(label=_("Face recognised"))
	title.get_style_context().add_class("howdy-title")
	title.set_margin_top(20)
	card.pack_start(title, False, False, 0)

	subtitle = Gtk.Label(label=_("Authorise this action as {}").format(username))
	subtitle.get_style_context().add_class("howdy-subtitle")
	subtitle.set_margin_top(6)
	card.pack_start(subtitle, False, False, 0)

	buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
	buttons.set_homogeneous(True)
	buttons.set_margin_top(30)
	buttons.set_margin_bottom(8)

	# Inset from the text block, so the row does not run to the card edge
	buttons.set_margin_start(14)
	buttons.set_margin_end(14)
	card.pack_start(buttons, False, False, 0)

	def finish(value):
		answer["value"] = value
		Gtk.main_quit()

	def make_button(text, key, classes):
		"""Button with the key that does the same thing tucked beside it"""
		label = Gtk.Label()
		label.set_markup("{}  <span alpha='45%' size='small'>{}</span>".format(
			GLib.markup_escape_text(text), GLib.markup_escape_text(key)))

		button = Gtk.Button()
		button.add(label)
		button.set_can_focus(False)

		for name in classes:
			button.get_style_context().add_class(name)

		return button

	cancel = make_button(_("Cancel"), abort_key.capitalize(), ["howdy-button"])
	cancel.connect("clicked", lambda _button: finish(False))
	buttons.pack_start(cancel, True, True, 0)

	accept = make_button(_("Authorise"), "↵", ["howdy-button", "howdy-confirm"])
	accept.connect("clicked", lambda _button: finish(True))
	buttons.pack_start(accept, True, True, 0)

	def on_key(_widget, event):
		if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
			finish(True)
		elif event.keyval == Gdk.KEY_Escape:
			finish(False)

		return True

	window.connect("key-press-event", on_key)

	# Closing the window is a refusal, not a pass
	window.connect("delete-event", lambda *_args: finish(False))

	def tick():
		left = deadline - time.time()

		if left <= 0:
			finish(not failsafe)
			return False

		remaining["fraction"] = left / timeout
		badge.queue_draw()
		return True

	GLib.timeout_add(40, tick)

	window.show_all()
	window.present()

	Gtk.main()
	window.destroy()

	return bool(answer["value"])


class confirm(RubberStamp):
	"""Ask for a deliberate confirmation after a face has been recognised"""

	def declare_config(self):
		"""Set the default values for the optional arguments"""
		self.options["abort_key"] = "esc"
		self.options["confirm_key"] = "enter"

	def run(self):
		"""Prompt on the user's desktop, or in the terminal when there is none"""
		try:
			username = sys.argv[1]
		except IndexError:
			return self.run_terminal()

		env = find_session(username)

		if env is None:
			return self.run_terminal()

		return self.run_window(username, env)

	def run_window(self, username, env):
		"""Run the window helper as the user and wait for its verdict"""
		entry = pwd.getpwnam(username)
		timeout = float(self.options["timeout"])

		# The window may open on another workspace, leave a trail behind
		self.set_ui_text(_("Confirm in the window on your desktop"), self.UI_TEXT)

		command = [
			sys.executable, os.path.abspath(__file__), username,
			str(timeout), "1" if self.options["failsafe"] else "0",
			self.options["abort_key"],
		]

		# The helper gets no inherited path, point it at howdy's modules
		env = dict(env, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

		try:
			# user and group drop the privileges between fork and exec, which
			# both satisfies GTK and keeps the window out of root's hands
			process = subprocess.Popen(
				command, env=env, user=entry.pw_uid, group=entry.pw_gid,
				extra_groups=os.getgrouplist(username, entry.pw_gid),
				stderr=subprocess.PIPE)
		except Exception as error:
			print(_("Could not start the confirmation window: {}").format(error), file=sys.stderr)
			return self.run_terminal()

		try:
			# The helper closes itself on timeout, this only catches a hang
			_stdout, stderr = process.communicate(timeout=timeout + 10)
		except subprocess.TimeoutExpired:
			process.kill()
			process.communicate()
			print(_("The confirmation window stopped responding"), file=sys.stderr)
			return not self.options["failsafe"]

		if process.returncode == EXIT_AUTHORISED:
			return True

		if process.returncode == EXIT_REFUSED:
			return False

		# Anything else means no window was drawn, say why and ask in the shell
		if stderr:
			print(stderr.decode("utf-8", "replace").strip(), file=sys.stderr)

		return self.run_terminal()

	def run_terminal(self):
		"""Ask in the terminal, for sessions without a desktop to draw on"""
		try:
			import keyboard
		except Exception as error:
			print(_("Missing the keyboard module for the confirm stamp: {}").format(error), file=sys.stderr)
			return False

		pressed = {"key": None}
		keyboard.add_hotkey(self.options["abort_key"], lambda: pressed.__setitem__("key", "abort"))
		keyboard.add_hotkey(self.options["confirm_key"], lambda: pressed.__setitem__("key", "confirm"))

		self.set_ui_text(_("Press {abort_key} to abort, {confirm_key} to authorise").format(
			abort_key=self.options["abort_key"], confirm_key=self.options["confirm_key"]), self.UI_SUBTEXT)

		time_left = float(self.options["timeout"])

		while time_left > 0:
			self.set_ui_text(_("Authorising in {}").format(int(time_left) + 1), self.UI_TEXT)

			if pressed["key"] == "abort":
				return False

			if pressed["key"] == "confirm":
				return True

			time.sleep(0.1)
			time_left -= 0.1

		return not self.options["failsafe"]


if __name__ == "__main__":
	# Invoked as the window helper: <username> <timeout> <failsafe> <abort_key>
	if len(sys.argv) != 5:
		print("usage: confirm.py <username> <timeout> <failsafe> <abort_key>", file=sys.stderr)
		sys.exit(EXIT_NO_WINDOW)

	try:
		granted = show_window(sys.argv[1], float(sys.argv[2]), sys.argv[3] == "1", sys.argv[4])
	except Exception as error:
		print("confirm window failed: {}".format(error), file=sys.stderr)
		sys.exit(EXIT_NO_WINDOW)

	sys.exit(EXIT_AUTHORISED if granted else EXIT_REFUSED)
