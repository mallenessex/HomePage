"""
Shared chat permission constants.
Keep bit definitions in one place for chat and admin routes.
"""

PERM_VIEW_CHANNEL = 1 << 0
PERM_SEND_MESSAGES = 1 << 1
PERM_MANAGE_CHANNELS = 1 << 2
PERM_MANAGE_ROLES = 1 << 3
PERM_MANAGE_EMOJIS = 1 << 4
PERM_CONNECT_VOICE = 1 << 5
PERM_SPEAK_VOICE = 1 << 6
PERM_ADMIN = 1 << 30

ALL_PERMISSIONS = (1 << 31) - 1

DEFAULT_MEMBER_PERMISSIONS = (
    PERM_VIEW_CHANNEL
    | PERM_SEND_MESSAGES
    | PERM_CONNECT_VOICE
    | PERM_SPEAK_VOICE
)

CHAT_PERMISSION_BITS = {
    "view_channel": PERM_VIEW_CHANNEL,
    "send_messages": PERM_SEND_MESSAGES,
    "manage_channels": PERM_MANAGE_CHANNELS,
    "manage_roles": PERM_MANAGE_ROLES,
    "manage_emojis": PERM_MANAGE_EMOJIS,
    "connect_voice": PERM_CONNECT_VOICE,
    "speak_voice": PERM_SPEAK_VOICE,
    "admin": PERM_ADMIN,
}
