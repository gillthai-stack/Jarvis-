"""Tools that see and control the Mac: the screen, browser, music, volume, Reminders, Notes and Shortcuts.

Everything goes through macOS's own command-line tools (screencapture, osascript, shortcuts).
User text is passed to AppleScript as arguments, never pasted into the script, so it cannot
change what the script does.
"""

import base64
import itertools
import json
import platform
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

SCREEN_MAX_PX = 1568  # Claude reads images up to about this size without downscaling


def _require_mac():
    if platform.system() != "Darwin":
        raise RuntimeError("This only works on macOS.")


def osascript(script: str, *args: str, timeout: int = 20) -> str:
    _require_mac()
    result = subprocess.run(["osascript", "-", *args], input=script, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        err = result.stderr.strip()
        if "-1743" in err or "Not authorized" in err:
            raise PermissionError(
                "macOS blocked this. Open System Settings > Privacy & Security > Automation and allow "
                "Terminal (or the app running Jarvis) to control the app."
            )
        raise RuntimeError(err or "AppleScript failed.")
    return result.stdout.strip()


def _running(app: str) -> bool:
    return osascript('on run argv\nreturn application (item 1 of argv) is running\nend run', app) == "true"


# ---------- screen ----------

def look_at_screen(_: dict) -> list:
    """Take a screenshot of the main display and return it as an image for Claude."""
    _require_mac()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "screen.jpg"
        subprocess.run(["screencapture", "-x", "-m", "-t", "jpg", str(path)], check=True, timeout=15)
        subprocess.run(["sips", "-Z", str(SCREEN_MAX_PX), str(path)], capture_output=True, check=True, timeout=15)
        data = base64.standard_b64encode(path.read_bytes()).decode()
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
        {"type": "text", "text": "Screenshot of the main display. If it shows only the desktop wallpaper, "
                                 "macOS needs Screen Recording permission for Terminal (System Settings > "
                                 "Privacy & Security > Screen Recording)."},
    ]


# ---------- browser ----------

BROWSERS = {
    "Safari": 'tell application "Safari" to return (URL of current tab of front window) & "\\n" & (name of current tab of front window)',
    "Google Chrome": 'tell application "Google Chrome" to return (URL of active tab of front window) & "\\n" & (title of active tab of front window)',
    "Arc": 'tell application "Arc" to return (URL of active tab of front window) & "\\n" & (title of active tab of front window)',
    "Microsoft Edge": 'tell application "Microsoft Edge" to return (URL of active tab of front window) & "\\n" & (title of active tab of front window)',
    "Brave Browser": 'tell application "Brave Browser" to return (URL of active tab of front window) & "\\n" & (title of active tab of front window)',
}


def browser_tab(_: dict) -> str:
    tabs = []
    for app, script in BROWSERS.items():
        if not _running(app):
            continue
        try:
            url, _, title = osascript(script).partition("\n")
            tabs.append({"browser": app, "url": url, "title": title})
        except RuntimeError:
            continue  # browser running with no windows
    if not tabs:
        return "No supported browser has an open window (Safari, Chrome, Arc, Edge, Brave)."
    return json.dumps(tabs, indent=1)


# ---------- media and volume ----------

MEDIA_COMMANDS = {"play": "play", "pause": "pause", "toggle": "playpause", "next": "next track", "previous": "previous track"}


def media_control(args: dict) -> str:
    action = args["action"]
    if action not in MEDIA_COMMANDS and action != "now_playing":
        raise ValueError(f"Unknown action {action}. Use play, pause, toggle, next, previous or now_playing.")
    player = next((app for app in ("Spotify", "Music") if _running(app)), None)
    if player is None:
        if action in ("play", "toggle"):
            player = "Music"
        else:
            return "Neither Spotify nor Music is open."
    if action == "now_playing":
        script = (f'tell application "{player}"\nif player state is playing then\n'
                  'return (name of current track) & " by " & (artist of current track)\n'
                  'else\nreturn "Nothing is playing."\nend if\nend tell')
        return f"{player}: {osascript(script)}"
    osascript(f'tell application "{player}" to {MEDIA_COMMANDS[action]}')
    return f"{player}: {action} done."


def set_volume(args: dict) -> str:
    level = max(0, min(100, args["level"]))
    osascript("on run argv\nset volume output volume (item 1 of argv as integer)\nend run", str(level))
    return f"Volume set to {level}%."


# ---------- Reminders, Notes, notifications ----------

def create_reminder(args: dict) -> str:
    script = """on run argv
set theTitle to item 1 of argv
set theDue to item 2 of argv
tell application "Reminders"
    if theDue is "" then
        make new reminder with properties {name:theTitle}
    else
        make new reminder with properties {name:theTitle, due date:date theDue}
    end if
end tell
return "ok"
end run"""
    due = args.get("due", "")
    if due:
        # AppleScript's date parser follows the Mac's locale, so hand it a fully spelled-out date.
        from datetime import datetime
        due = datetime.fromisoformat(due).strftime("%A, %d %B %Y at %H:%M:%S")
    osascript(script, args["title"], due)
    return f"Reminder added: {args['title']}" + (f" (due {args['due']})" if args.get("due") else "")


def create_note(args: dict) -> str:
    script = """on run argv
tell application "Notes" to make new note at default account with properties {name:(item 1 of argv), body:(item 2 of argv)}
return "ok"
end run"""
    body = "<h1>{}</h1>{}".format(_html(args["title"]), "".join(f"<div>{_html(line) or '<br>'}</div>"
                                                               for line in args["body"].splitlines()))
    osascript(script, args["title"], body)
    return f"Note '{args['title']}' created in Notes."


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def notify(args: dict) -> str:
    osascript('on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run',
              args.get("title", "Jarvis"), args["message"])
    return "Notification shown."


# ---------- clipboard ----------

MAX_CLIPBOARD_CHARS = 20_000


def read_clipboard(_: dict) -> str:
    _require_mac()
    text = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=10).stdout
    if not text.strip():
        return "The clipboard has no text."
    return text[:MAX_CLIPBOARD_CHARS] + (f"\n\n[cut off: {len(text)} characters]" if len(text) > MAX_CLIPBOARD_CHARS else "")


def copy_to_clipboard(args: dict) -> str:
    _require_mac()
    text = args["text"]
    if len(text) > MAX_CLIPBOARD_CHARS:
        raise ValueError(f"That is longer than {MAX_CLIPBOARD_CHARS} characters.")
    subprocess.run(["pbcopy"], input=text, text=True, timeout=10, check=True)
    return f"Copied {len(text)} characters to the clipboard."


# ---------- timers ----------

MAX_TIMERS = 20
MAX_TIMER_MINUTES = 24 * 60
_timers: dict[str, dict] = {}
_timer_ids = itertools.count(1)
_timer_lock = threading.Lock()


def _ring(timer_id: str) -> None:
    with _timer_lock:
        t = _timers.pop(timer_id, None)
    if t is None:
        return
    label = t["label"] or "Timer"
    try:
        osascript('on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"\n'
                  'end run', "Jarvis timer", f"{label}: time's up.")
        # The label goes in as a file, never on the command line.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(f"{label.replace('[[', ' ')}. Time's up.")
        subprocess.run(["/usr/bin/say", "-f", f.name], timeout=30)
        Path(f.name).unlink(missing_ok=True)
    except Exception:
        pass  # the notification is best effort; nothing is waiting on it


def set_timer(args: dict) -> str:
    _require_mac()
    minutes = args["minutes"]
    if not 0 < minutes <= MAX_TIMER_MINUTES:
        raise ValueError("Timers can be from a few seconds up to 24 hours (minutes between 0.1 and 1440).")
    label = args.get("label", "").strip()[:80]
    with _timer_lock:
        if len(_timers) >= MAX_TIMERS:
            raise RuntimeError(f"There are already {MAX_TIMERS} timers running.")
        timer_id = f"timer{next(_timer_ids)}"
        due = datetime.now() + timedelta(minutes=minutes)
        handle = threading.Timer(minutes * 60, _ring, args=(timer_id,))
        handle.daemon = True
        _timers[timer_id] = {"label": label, "due": due, "handle": handle}
        handle.start()
    return (f"Timer {timer_id} set for {minutes:g} minute{'s' if minutes != 1 else ''}"
            f"{f' ({label})' if label else ''}, ringing at {due:%H:%M:%S}. Timers stop if Jarvis is restarted.")


def list_timers(_: dict) -> str:
    with _timer_lock:
        if not _timers:
            return "No timers are running."
        now = datetime.now()
        return json.dumps([{"id": i, "label": t["label"], "rings_at": f"{t['due']:%H:%M:%S}",
                            "seconds_left": max(0, round((t["due"] - now).total_seconds()))}
                           for i, t in _timers.items()], indent=1)


def cancel_timer(args: dict) -> str:
    with _timer_lock:
        t = _timers.pop(args["id"], None)
    if t is None:
        raise ValueError(f"No running timer {args['id']}. Use list_timers to see them.")
    t["handle"].cancel()
    return f"Cancelled {args['id']}."


# ---------- Shortcuts ----------

def list_shortcuts(_: dict) -> str:
    _require_mac()
    out = subprocess.run(["shortcuts", "list"], capture_output=True, text=True, timeout=20).stdout.strip()
    return out or "No shortcuts found."


def run_shortcut(args: dict) -> str:
    _require_mac()
    cmd = ["shortcuts", "run", args["name"]]
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "out.txt"
        cmd += ["--output-path", str(out_path)]
        if args.get("input"):
            in_path = Path(tmp) / "in.txt"
            in_path.write_text(args["input"])
            cmd += ["--input-path", str(in_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Shortcut '{args['name']}' failed.")
        output = out_path.read_text(errors="ignore").strip() if out_path.exists() else ""
    return f"Ran shortcut '{args['name']}'." + (f" Output:\n{output[:4000]}" if output else "")


def tools(tool) -> dict:
    return {
        "look_at_screen": tool(
            look_at_screen,
            "Take a screenshot of the user's main display and look at it. Use when the user asks about "
            "what is on their screen, an error they are seeing, or the window they are working in.",
        ),
        "browser_tab": tool(browser_tab, "Get the URL and title of the active tab in each open browser."),
        "media_control": tool(
            media_control,
            "Control Spotify or Apple Music: play, pause, toggle, next, previous, or now_playing.",
            {"action": {"type": "string", "enum": ["play", "pause", "toggle", "next", "previous", "now_playing"]}},
        ),
        "set_volume": tool(set_volume, "Set the Mac's output volume.", {"level": {"type": "integer", "description": "0 to 100"}}),
        "create_reminder": tool(
            create_reminder,
            "Add a reminder to Apple Reminders, optionally with a due date and time.",
            {"title": {"type": "string"}, "due": {"type": "string", "description": "Local time, YYYY-MM-DDTHH:MM"}},
            optional=("due",),
        ),
        "create_note": tool(
            create_note, "Create a note in Apple Notes.", {"title": {"type": "string"}, "body": {"type": "string"}},
        ),
        "notify": tool(
            notify, "Show a macOS notification banner.",
            {"title": {"type": "string"}, "message": {"type": "string"}}, optional=("title",),
        ),
        "read_clipboard": tool(read_clipboard, "Read the text currently on the Mac's clipboard."),
        "copy_to_clipboard": tool(
            copy_to_clipboard, "Put text on the Mac's clipboard so the user can paste it.", {"text": {"type": "string"}},
        ),
        "set_timer": tool(
            set_timer,
            "Start a countdown timer. When it ends the Mac shows a notification and says the label out loud.",
            {"minutes": {"type": "number", "description": "Length in minutes, e.g. 0.5 for 30 seconds"},
             "label": {"type": "string", "description": "What the timer is for, e.g. 'pasta'"}},
            optional=("label",),
        ),
        "list_timers": tool(list_timers, "List running timers and how long each has left."),
        "cancel_timer": tool(cancel_timer, "Cancel a running timer by its id.", {"id": {"type": "string"}}),
        "list_shortcuts": tool(list_shortcuts, "List the user's Apple Shortcuts by name."),
        "run_shortcut": tool(
            run_shortcut,
            "Run one of the user's Apple Shortcuts by its exact name, optionally passing text input. "
            "Check list_shortcuts first if unsure of the name.",
            {"name": {"type": "string"}, "input": {"type": "string"}},
            optional=("input",),
        ),
    }
