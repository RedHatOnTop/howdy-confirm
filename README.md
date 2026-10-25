# howdy-confirm

**English** | [한국어](README.ko.md)

A confirmation step for [Howdy](https://github.com/boltgolt/howdy) face authentication. Once
your face is recognised, a prompt on your desktop asks you to authorise the action, so being in
front of the camera is not enough on its own.

![The prompt: a face glyph inside a draining ring, "Face recognised", and Cancel and Authorise buttons](docs/confirm.png)

Howdy lets you in the moment the IR camera sees you. That is the point at a login screen and a
problem everywhere else: a script you started calls `sudo`, and it goes through because you
happened to be facing the laptop. This is a Howdy *rubber stamp*, Howdy's hook for checks that
run after a face matches, and it asks first.

- **Enter** or **Authorise** lets the action through; **Esc**, **Cancel** or closing the window
  refuses it.
- The ring drains over the timeout. When it runs out, a `failsafe` rule refuses and a
  `faildeadly` rule lets the action through.
- With no desktop session to draw on, it asks in the terminal instead.

## Install

```sh
git clone https://github.com/RedHatOnTop/howdy-confirm.git
cd howdy-confirm
sudo ./install.sh
```

Then turn it on in `/etc/howdy/config.ini`:

```ini
[rubberstamps]
enabled = true
stamp_rules =
	confirm	10s	failsafe	confirm_key=enter abort_key=esc
```

`install.sh` copies `confirm.py` next to Howdy's own stamps and applies
`rubberstamps-terminal.patch` to Howdy's stamp loader, keeping the original as
`__init__.py.orig`. `sudo ./install.sh --remove` takes both out again. If Howdy lives somewhere
the script does not look, pass `--howdy-dir`.

An update of the Howdy package replaces the loader and drops the patch (`rpm -V howdy` shows the
file as changed while it is in). Run `install.sh` again after one.

## Requirements

- Howdy 3.0. Tested with 3.0.0 on Fedora 44, GNOME 50 on Wayland, for `sudo`.
- GTK 3 through PyGObject, and pycairo, for the window (`python3-gobject` and `python3-cairo`
  on Fedora).
- Howdy's `keyboard` module for the terminal prompt.
- `git`, which `install.sh` uses to apply the patch.

## How it works

Two things make a desktop prompt from inside a PAM module awkward.

`pam_howdy` starts Howdy's compare process with an empty environment
(`posix_spawnp(..., envp = NULL)`), so there is no `WAYLAND_DISPLAY` or `DISPLAY` to go on. The
username is the one thing it is given, and the stamp finds the session from that through
`/run/user/<uid>`.

The compare process also inherits the setuid state of whatever runs the PAM stack. Under `sudo`
that means `ruid != euid`, and GTK refuses to start in a process like that. So the window is drawn
by a helper: the same file, run again as the user being authenticated, with privileges dropped
between fork and exec. That is what GTK wants, and less privilege than drawing the window as root.
The helper answers with its exit code.

## The loader patch

Howdy shows a stamp's text only through `howdy-gtk`. Without it, a stamp's prompt is drawn
nowhere, the stamp times out, and the log says only "unknown error 15". The patch writes the
text to stderr when `howdy-gtk` is absent, which reaches the terminal through PAM, and prints a
countdown line only when it changes. It also makes one regular expression a raw string, which
newer Pythons warn about. Howdy's own `nod` and `hotkey` stamps benefit the same way.

## Limits

- It guards against authorising by accident, not against software already running as you. Such
  software can press Authorise itself (on GNOME, Mutter's remote desktop API lets any of your
  processes drive input), so this is a check that every use is deliberate, not a boundary
  against your own account.
- It does not work at the GDM login screen, where your session does not exist yet.
- The helper takes the first `wayland-*` socket in your runtime directory, or else the lowest X
  display. The X11 branch, the terminal prompt and hosts with several graphical sessions have not
  been run yet.

## License

MIT, like Howdy.
