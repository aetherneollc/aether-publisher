"""Centralized TikTok web-upload selectors.

TikTok's upload UI (creator tools at tiktok.com) changes its DOM more often
than YouTube Studio's, and unlike YouTube we did not have an existing
MIT-licensed implementation to port from for this platform (social-auto-
upload's TikTok uploader targets a different Chrome-profile flow, and its
current selector set is not stable enough upstream to be worth porting
verbatim — see THIRD_PARTY_NOTICES.md). These selectors are original,
written from TikTok's known `data-e2e` testid convention plus generic
fallbacks, and WILL need verification/updates against the live site before
first real use — that is expected and is exactly why they are centralized
here rather than scattered through adapter.py.
"""

from __future__ import annotations

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp&lang=en"
LOGIN_URL_MARKERS = ["/login", "tiktok.com/login"]

SEL = {
    "file_input": 'input[type="file"]',
    # Caption editor is a Draft.js contenteditable div.
    "caption_editor": 'div[data-e2e="video-caption"] div[contenteditable="true"], '
    'div.public-DraftEditor-content[contenteditable="true"]',
    # Shown while TikTok is transcoding/processing the uploaded file client-side.
    "upload_progress": '[data-e2e="upload-progress"], .info-progress, .progress-container',
    "upload_success_marker": (
        "text=Uploaded, text=上传完成, [data-e2e='video-uploaded'], .upload-success"
    ),
    "schedule_radio_now": '[data-e2e="schedule-switch"] input[value="now"], input[name="schedule"][value="0"]',
    "schedule_radio_later": '[data-e2e="schedule-switch"] input[value="schedule"], input[name="schedule"][value="1"]',
    "schedule_date_picker": '[data-e2e="schedule-date-picker"]',
    "schedule_time_picker": '[data-e2e="schedule-time-picker"]',
    "post_button": 'button[data-e2e="post_video_button"], button:has-text("Post")',
    "post_success_toast": 'text=Your video is being uploaded, text=已发布, [data-e2e="upload-success-toast"]',
    "manage_content_link": 'a[href*="/tiktokstudio/content"], a:has-text("Manage your content")',
    # Video page (after navigating to your own content list -> most recent item).
    "content_list_first_item": '[data-e2e="content-item"]:first-of-type a, .content-item:first-child a',
    "comment_input": '[data-e2e="comment-input"], div[contenteditable="true"][data-e2e="comment-text"]',
    "comment_post_button": 'button[data-e2e="comment-post"], button:has-text("Post")',
    "comment_more_menu": '[data-e2e="comment-more"], button[aria-label="More"]',
    "comment_pin_option": 'text=Pin, [data-e2e="comment-pin"]',
}
