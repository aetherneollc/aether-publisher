"""Centralized YouTube Studio selectors.

Kept in one module, separate from the step logic in `adapter.py`, on
purpose: YouTube Studio's DOM (it's built on `ytcp-*` / `tp-yt-*` custom
elements) drifts over time, and when it does, this is the one file that
needs re-inspecting in devtools — not a search-and-replace through the whole
adapter.

Several of these selectors are adapted from the MIT-licensed
`social-auto-upload` project's `YouTubeVideo.upload()`
(uploader/youtube_uploader/main.py) — see ../../../THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

STUDIO_URL = "https://studio.youtube.com"
UPLOAD_URL = "https://www.youtube.com/upload"

VISIBILITY_RADIO_NAME = {
    "public": "PUBLIC",
    "unlisted": "UNLISTED",
    "private": "PRIVATE",
}

LOGIN_URL_MARKERS = ["accounts.google.com", "/signin"]
LOGGED_IN_URL_MARKER = "/channel/"

SEL = {
    "file_input": 'input[type="file"]',
    "title_textarea_wrapper": "#title-textarea",
    "title_textbox": "#title-textarea #textbox",
    "description_textbox": "#description-textarea #textbox",
    "thumbnail_input": "#file-loader input[type='file'], ytcp-thumbnail-uploader input[type='file']",
    "autocomplete_dropdown": "tp-yt-iron-dropdown:visible",
    "playlist_dropdown_trigger": (
        "#basics ytcp-text-dropdown-trigger, ytcp-video-metadata-playlists ytcp-dropdown-trigger"
    ),
    "playlist_new_button": "ytcp-button:has-text('New playlist'), ytcp-button:has-text('创建播放列表')",
    "playlist_new_menu_item": (
        "tp-yt-paper-item:has-text('New playlist'), tp-yt-paper-item:has-text('新建播放列表')"
    ),
    "playlist_title_box": "ytcp-playlist-metadata-editor #textbox, #create-playlist-form #textbox",
    "playlist_create_button": (
        "ytcp-button#create-button, tp-yt-paper-dialog ytcp-button:has-text('Create'), "
        "tp-yt-paper-dialog ytcp-button:has-text('创建')"
    ),
    "playlist_done_button": "ytcp-playlist-dialog #save-button, ytcp-button:has-text('Done'), ytcp-button:has-text('完成')",
    "audience_not_for_kids": "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
    "audience_not_for_kids_fallback": (
        "tp-yt-paper-radio-button:has-text('not made for kids'), "
        "tp-yt-paper-radio-button:has-text('不是面向儿童')"
    ),
    "tags_toggle_more": "#toggle-button",
    "tags_input": "#tags-container #text-input, ytcp-form-input-container#tags-container input",
    "next_button": "#next-button",
    "visibility_radio_public_probe": "tp-yt-paper-radio-button[name='PUBLIC']",
    "visibility_radio": "tp-yt-paper-radio-button[name='{name}']",
    "upload_progress_text": ".progress-label, span.progress-label, ytcp-video-upload-progress",
    "done_button": "#done-button",
    "close_button": "ytcp-button:has-text('Close'), ytcp-button:has-text('关闭'), #close-button",
    "published_video_link": "a[href*='youtu.be'], a[href*='watch?v=']",
}

# Substrings in the upload-progress label that mean "the upload itself has
# finished" (processing/checks afterwards is fine to publish through).
UPLOAD_DONE_MARKERS = ("处理", "检查", "上传完成", "已上传", "Processing", "complete", "Checks", "Finished")
