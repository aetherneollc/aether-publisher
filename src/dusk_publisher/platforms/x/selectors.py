"""Centralized X (Twitter) web-compose selectors.

social-auto-upload has no X/Twitter uploader (see THIRD_PARTY_NOTICES.md) —
everything in this module is our own, based on X's own `data-testid`
convention, which has been comparatively stable for the core compose/post
flow for years (unlike the more volatile "Highlight"/pin-reply affordance,
called out below). As with the TikTok selectors, expect to need to verify
these against the live site before first real use; centralized here so
that's a one-file fix.
"""

from __future__ import annotations

HOME_URL = "https://x.com/home"
LOGIN_URL = "https://x.com/login"
LOGIN_URL_MARKERS = ["/login", "/i/flow/login"]

SEL = {
    "compose_button": '[data-testid="SideNav_NewTweet_Button"]',
    "tweet_textarea": '[data-testid="tweetTextarea_0"]',
    "file_input": 'input[data-testid="fileInput"]',
    # Shown on the attached-media thumbnail while X is transcoding the video.
    "media_processing_spinner": '[data-testid="attachments"] [role="progressbar"], [aria-label="Uploading"]',
    "remove_media_button": '[data-testid="removeMedia"], button[aria-label="Remove media"]',
    "post_button": '[data-testid="tweetButtonInline"], [data-testid="tweetButton"]',
    "toast_view_link": '[data-testid="toast"] a[href*="/status/"]',
    # Fallback: newest tweet link on your own profile timeline.
    "profile_link": '[data-testid="AppTabBar_Profile_Link"]',
    "first_tweet_permalink": 'article[data-testid="tweet"] a[href*="/status/"]',
    "reply_button_on_tweet": '[data-testid="reply"]',
    # NOTE: X's "highlight a reply" (its closest equivalent to a pinned top
    # comment) is a less standardized / more frequently-restyled affordance
    # than YouTube's pinned-comment or TikTok's pin-comment menu item. This
    # is best-effort and may simply no-op on some accounts/UI versions.
    "reply_caret_menu": '[data-testid="caret"]',
    "highlight_menu_item": '[role="menuitem"]:has-text("Highlight")',
}
