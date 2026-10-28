# Optional application emoji assets

Place PNG, GIF or JPG files here, named after the logical names in
`nex/services/emoji.py` (for example `success.png`). Each file must be at most
256 KiB. No image assets are required; the bot always has Unicode fallbacks.
Set APPLICATION_EMOJI_PROVISIONING=true to opt into uploads. Existing matching
application emojis are reused even when uploads are disabled.
