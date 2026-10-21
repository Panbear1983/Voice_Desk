"""Voice Desk — one place for every tunable.

Plain constants, no config file: Peter asked for a few presets, not a page of knobs.
The two presets that matter are the listening mode (open mic / tap to talk) and the
sleep timer; both are switchable from inside the window.
"""
from __future__ import annotations

import os
import pwd
from pathlib import Path

HOME = Path.home()
REPO = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("VOICE_DESK_STATE", HOME / ".voice_desk"))
_OLD_STATE_DIR = HOME / ".claude_voice"            # the folder's name before the app became Voice Desk
if "VOICE_DESK_STATE" not in os.environ and not STATE_DIR.exists() and _OLD_STATE_DIR.is_dir():
    _OLD_STATE_DIR.rename(STATE_DIR)               # keeps the threads, the phone token and the transcript
STATE_DIR.mkdir(parents=True, exist_ok=True)

# ---- microphone / silence detection -------------------------------------------------
SAMPLE_RATE = 16_000            # whisper wants 16 kHz mono
BLOCK_MS = 30                   # one analysis slice
BLOCK = SAMPLE_RATE * BLOCK_MS // 1000
PRE_ROLL_SEC = 0.6              # audio kept from before speech was detected
SPEECH_START_SEC = 0.25         # how long sound must stay loud before we call it speech
SILENCE_SEND_SEC = 1.5          # Peter's pause that acts as "send"
MAX_UTTERANCE_SEC = 90          # hard stop so a stuck mic cannot record forever
MIN_SPEECH_SEC = 0.4            # anything shorter is a cough or a click
NOISE_FLOOR_START = 150.0       # int16 RMS guess before calibration
SPEECH_OVER_FLOOR = 3.0         # speech = louder than floor * this (and above MIN_SPEECH_RMS)
MIN_SPEECH_RMS = 350.0
BARGE_IN_OVER_FLOOR = 4.0       # while Claude speaks, the mic hears the speakers too, so be stricter
BARGE_IN_SEC = 0.5

# ---- sleep / wake --------------------------------------------------------------------
SLEEP_AFTER_SEC = 60            # no speech for this long -> grey light, wake word needed
WAKE_WORDS = ("hey claude", "hey, claude", "claude", "hey hermes", "hey, hermes", "克勞德", "克劳德")

# ---- spoken commands (matched after lowercasing and stripping punctuation) ------------
STOP_WORDS = {"stop", "wait", "hold on", "stop talking", "shut up", "quiet", "等一下", "停"}
GOODBYE_WORDS = {"goodbye", "good bye", "bye", "bye claude", "bye bye", "that's all", "that is all", "再見", "拜拜"}
SLEEP_WORDS = {"go to sleep", "sleep now", "stop listening", "休息"}
OPEN_MIC_WORDS = {"open mic", "open microphone", "hands free"}
TAP_MODE_WORDS = {"push to talk", "tap to talk", "tap mode"}
TRAILING_SEND_WORDS = ("over", "over.", "send", "send.")

# ---- speech to text ------------------------------------------------------------------
WHISPER_SERVER = "/opt/homebrew/bin/whisper-server"
WHISPER_MODEL = HOME / ".local/share/whisper-models/ggml-small.bin"   # 1.2 s per turn; large-v3-turbo is 4.5 s
WHISPER_PORT = int(os.environ.get("VOICE_DESK_WHISPER_PORT", "8178"))
WHISPER_THREADS = 8
WHISPER_LANGUAGE = "auto"       # English and Mandarin both land correctly
# A hint of the words to expect. Without it the small model hears "Javas", "Piley" and
# "Juanaba Faye" (measured 2026-10-21 with three Mac voices); with it all four names land.
# Mandarin still comes back as Mandarin.
WHISPER_PROMPT = "Jarvis, Kylie, Esther, Wanna Buffet, Claude, Gemini. Mute, unmute."

# whisper invents these on silence / breaths; drop them.
WHISPER_HALLUCINATIONS = {
    "", "thank you.", "thank you", "thanks for watching.", "thanks for watching", "you", "you.",
    "bye.", "bye", "[blank_audio]", "(silence)", "[silence]", "[music]", "(music)", "字幕由amara.org社群提供",
    "請不吝點贊 訂閱 轉發 打賞支持明鏡與點點欄目", "謝謝觀看", "谢谢观看",
}

# ---- text to speech ------------------------------------------------------------------
# Hermes' voice chain, the same script the Telegram bots use: Microsoft Edge neural voices
# through the new host (~/.local/bin/edge_tts_newhost.py), then Gemini, then a Mac voice.
# (The older tts_jenny_or_mac.sh still dials Edge's dead host and falls through to Piper.)
TTS_CHAIN = HOME / ".hermes/scripts/tts_natural.sh"
TTS_ORDER = os.environ.get("VOICE_DESK_TTS_ORDER", "edge google mac")
TTS_FALLBACK_STATE = HOME / ".hermes/cache/tts_fallback"   # where the chain benches a voice for 10 minutes
TTS_VOICE_EN = "en-US-JennyNeural"   # the shared voice: used when per-profile voices are off (dashboard V)
TTS_VOICE_ZH_MAC = "Meijia"      # Taiwan Mandarin, the Mac fallback when Edge is down
AFPLAY = "/usr/bin/afplay"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
STATUS_EVERY_SEC = 10           # spoken "still working" while Claude thinks

# ---- the brain -----------------------------------------------------------------------
CLAUDE_BIN = os.environ.get("VOICE_DESK_CLAUDE_BIN", "claude")
CLAUDE_CWD = HOME               # the thread runs in Peter's home, so every repo is reachable
CLAUDE_TURN_TIMEOUT_SEC = 600
SESSION_FILE = STATE_DIR / "session_id"          # one thread shared by the Mac window and the phone
DEFAULT_MODEL = "claude-fable-5-1"
MODEL_FILE = STATE_DIR / "model"                 # set by voice: "use Opus", "switch to Sonnet", "which model"
DEFAULT_EFFORT = "medium"                        # voice presets: quick (low) / normal (medium) / deep (high) / max
EFFORT_FILE = STATE_DIR / "effort"               # set by voice: "think deep", "quick mode", "which effort"
TURN_LOCK_FILE = STATE_DIR / "turn.lock"
TRANSCRIPT_LOG = STATE_DIR / "transcript.log"

# who Claude is talking to: VOICE_DESK_NAME, else the first word of the Mac account's full name
USER_NAME = os.environ.get("VOICE_DESK_NAME") or (pwd.getpwuid(os.getuid()).pw_gecos.split() or ["the user"])[0]

VOICE_SYSTEM_PROMPT = f"""You are talking with {USER_NAME} through a live voice loop. {USER_NAME} hears you and is not reading a screen.
Reply the way you would speak on the phone: plain spoken English (answer in Mandarin if they spoke Mandarin), one to four short sentences, lead with the outcome.
No markdown, no bullet lists, no headings, no code blocks, no file paths, no URLs, no emojis, nothing that cannot be read aloud.
If the real answer needs code or long detail, do the work, write the detail to the file ~/.voice_desk/detail.md, and say in one sentence that the detail is in the window.
Do not ask more than one question at a time. Never end with a summary of what you just said."""

# ---- the other brains: Hermes profiles --------------------------------------------------
# Every Hermes profile on this Mac is its own separate thread. Say "talk to Esther" /
# "talk to Claude" to switch; each phone Shortcut is pinned to one of them.
HERMES_BIN = os.environ.get("VOICE_DESK_HERMES_BIN", str(HOME / ".local/bin/hermes"))
HERMES_PROFILES_DIR = Path(os.environ.get("HERMES_PROFILES_DIR", HOME / ".hermes/profiles"))
HERMES_SKIP_PROFILES = {"default"}                # lives at ~/.hermes itself and has no session store
# ---- names: who answers to what ------------------------------------------------------------
# Saying a name at the start of a sentence ("Jarvis, what time is it", "hey Kylie", "talk to
# Esther") wakes the line AND switches to that thread. Each entry: target -> (the name spoken
# back, every spelling whisper has been heard to produce for it). Names Peter picked 2026-10-21.
NAMES = {
    "claude": ("Claude", ("claude", "claud", "clod", "claude code", "克勞德", "克劳德")),
    "gemini": ("Gemini", ("gemini", "jemini", "google gemini")),
    "hermes:orchestrator": ("Jarvis", ("jarvis", "javis", "jaarvis", "javas", "jervis", "jarvas", "orchestrator")),
    "hermes:assistant": ("Kylie", ("kylie", "kiley", "kylee", "kyley", "assistant")),
    "hermes:tax_angel_esther": ("Esther", ("esther", "ester", "tax angel esther", "tax angel")),
    "hermes:wanna-buffet": ("Wanna Buffet", ("wanna buffet", "wanna buffett", "wannabuffet", "wana buffet",
                                             "wanna buff it", "warren buffett", "warren buffet")),
}
HERMES_DISPLAY_NAMES = {t.split(":", 1)[1]: n for t, (n, _) in NAMES.items() if t.startswith("hermes:")}
HERMES_TURN_TIMEOUT_SEC = 600
# Peter 2026-10-21: "Voice Desk is responding way too slow". Wanna Buffet runs reasoning_effort
# high for trading; a spoken question does not need it. Every Hermes voice turn uses this level
# instead ("" = the profile's own). Measured: a one-word reply ~5-9 s either way, the long tail
# (29 s on a trading question) is thinking time.
HERMES_VOICE_REASONING = os.environ.get("VOICE_DESK_HERMES_REASONING", "low")
HERMES_SESSION_DIR = STATE_DIR / "hermes_sessions"      # one session id per profile
BRAIN_FILE = STATE_DIR / "brain"                        # "claude" or "hermes:<profile>", set by voice: "talk to Esther"
HERMES_VOICE_NOTE = (f"(Voice call with {USER_NAME}: they hear you and are not reading a screen. Answer the way you "
                     "would speak on the phone, one to four short sentences, in Mandarin if they spoke Mandarin, "
                     "lead with the outcome. No markdown, lists, code, file paths, URLs or emojis.)")

# ---- Gemini, through Google's Antigravity command-line tool ---------------------------------
# Google retired the old Gemini CLI for individual subscribers; `agy` is its replacement and
# runs the Gemini models on the same subscription. Absent -> Gemini is simply not offered.
GEMINI_BIN = os.environ.get("VOICE_DESK_GEMINI_BIN", str(HOME / ".local/bin/agy"))
GEMINI_MODEL = "gemini-3.8-flash-medium"          # `agy models` lists the choices
GEMINI_TURN_TIMEOUT_SEC = 600
GEMINI_SESSION_FILE = STATE_DIR / "gemini_session"

# ---- the always-on listener (listener.py, run by launchd) ----------------------------------
# Sleeps with the mic open, listening only for a name; runs one turn; speaks; sleeps again.
LISTENER_LABEL = "com.peter.voice-desk-listener"
LISTENER_STATUS = STATE_DIR / "listener.json"     # what the dashboard shows; written by the listener only
LISTENER_INBOX = STATE_DIR / "listener.inbox"      # one-word orders from the dashboard: hush, new_thread
MUTE_FILE = STATE_DIR / "muted"                    # exists = muted (spoken "mute", or M in the dashboard)
ECHO_TAIL_SEC = 1.0             # the mic stays deaf this long after any speech ends (room echo)
# Peter 2026-10-21: after every reply, keep the line open this long so the back-and-forth needs no
# name; silence that long ends the conversation (falling tone). "goodbye" / "go to sleep" end it at once.
CONVERSATION_SEC = 20
# Audible turn-taking (Peter: "a beeping signal to know when my prompt gets cut ... before it goes
# into thinking"). Mac system sounds, played through afplay (the listener is deaf while they play).
SOUND_GOT_IT = "/System/Library/Sounds/Tink.aiff"       # your words were cut off here and sent
SOUND_YOUR_TURN = "/System/Library/Sounds/Pop.aiff"     # it finished speaking: talk now, no name needed
SOUND_ASLEEP = "/System/Library/Sounds/Bottle.aiff"     # conversation over: say hey <name> next time
# Speech from anything else on this Mac (the Claude Stop hook, Hermes voice mode, kokoro) plays
# through these programs; while one runs the listener is deaf, so it can never hear itself.
FOREIGN_PLAYERS = ("afplay", "say")
FOLLOW_UP_SEC = 10             # after a bare name ("Jarvis?" -> "Jarvis here.") the question needs no name for this long
ASLEEP_MAX_UTTERANCE_SEC = 20   # while asleep only short sentences matter: a name comes first
MUTE_WORDS = {"mute", "mute yourself", "mute the mic", "mute microphone", "mute please", "go mute",
              "mewed", "mude", "muse", "靜音", "静音"}
UNMUTE_WORDS = {"unmute", "un mute", "unmute yourself", "unmute please", "unmuted", "on mute", "on mude",
                "done mute", "done mewed", "listen again", "start listening", "you can listen", "取消靜音", "取消静音"}

# ---- phone receiver ------------------------------------------------------------------
RECEIVER_HOST = os.environ.get("VOICE_DESK_RECEIVER_HOST", "0.0.0.0")   # Tailscale + LAN; token-protected
RECEIVER_PORT = int(os.environ.get("VOICE_DESK_RECEIVER_PORT", "8765"))
RECEIVER_TOKEN_FILE = STATE_DIR / "receiver_token"
