"""Playwright browser automation fallback for Google Meet.

Used when the Meet Media API is unavailable. Joins Meet via headless
Chrome, scrapes captions for transcription, responds via chat.
"""
import asyncio
import logging
from typing import Any

from app.config import (
    CAPTION_POLL_INTERVAL_MS,
    MEET_BOT_DISPLAY_NAME,
    PLAYWRIGHT_HEADLESS,
)

logger = logging.getLogger(__name__)


class PlaywrightMeetBot:
    """Joins Google Meet via browser automation and scrapes captions."""

    def __init__(self):
        self._browser = None
        self._page = None
        self._context = None
        self._running = False
        self._seen_captions: set[str] = set()
        self._poll_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._running

    async def join(self, meet_link: str, display_name: str = MEET_BOT_DISPLAY_NAME) -> None:
        """Join a Google Meet via headless browser.

        Args:
            meet_link: Full Google Meet URL.
            display_name: Name displayed in the meeting.
        """
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        self._browser = await pw.chromium.launch(
            headless=PLAYWRIGHT_HEADLESS,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-notifications",
            ],
        )

        self._context = await self._browser.new_context(
            permissions=["camera", "microphone"],
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        self._page = await self._context.new_page()

        logger.info("Navigating to Meet: %s", meet_link)
        await self._page.goto(meet_link, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        try:
            name_input = await self._page.query_selector('input[aria-label="Your name"]')
            if name_input:
                await name_input.fill("")
                await name_input.type(display_name, delay=50)
                logger.info("Entered display name: %s", display_name)
        except Exception:
            logger.debug("No name input found (may already be signed in)")

        try:
            await self._dismiss_pre_join_dialogs()
        except Exception:
            logger.debug("No pre-join dialogs to dismiss")

        join_selectors = [
            'button:has-text("Join now")',
            'button:has-text("Ask to join")',
            'button:has-text("Join")',
            '[data-is-muted] + div button',
        ]
        joined = False
        for selector in join_selectors:
            try:
                btn = await self._page.wait_for_selector(selector, timeout=5000)
                if btn:
                    await btn.click()
                    joined = True
                    logger.info("Clicked join button: %s", selector)
                    break
            except Exception:
                continue

        if not joined:
            raise RuntimeError("Could not find join button in Google Meet")

        await asyncio.sleep(5)

        try:
            await self._enable_captions()
        except Exception as exc:
            logger.warning("Could not enable captions: %s", exc)

        self._running = True
        logger.info("Successfully joined Meet via browser")

    async def _dismiss_pre_join_dialogs(self) -> None:
        dismiss_selectors = [
            'button:has-text("Got it")',
            'button:has-text("Dismiss")',
            'button:has-text("Close")',
        ]
        for selector in dismiss_selectors:
            try:
                btn = await self._page.query_selector(selector)
                if btn:
                    await btn.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

    async def _enable_captions(self) -> None:
        cc_selectors = [
            'button[aria-label="Turn on captions"]',
            'button[aria-label="Turn on captions (c)"]',
            'button:has-text("Turn on captions")',
        ]
        for selector in cc_selectors:
            try:
                btn = await self._page.wait_for_selector(selector, timeout=5000)
                if btn:
                    await btn.click()
                    logger.info("Captions enabled")
                    return
            except Exception:
                continue
        logger.warning("Could not find captions button")

    async def poll_captions(self) -> list[dict[str, str]]:
        """Poll the DOM for new caption text.

        Returns:
            List of new caption entries with speaker and text.
        """
        if not self._page or not self._running:
            return []

        new_entries = []
        try:
            caption_elements = await self._page.query_selector_all(
                '[class*="caption"], [class*="subtitle"], [data-self-name]'
            )

            for elem in caption_elements:
                text = await elem.inner_text()
                text = text.strip()
                if not text or text in self._seen_captions:
                    continue
                self._seen_captions.add(text)

                parts = text.split("\n", 1)
                if len(parts) == 2:
                    speaker, content = parts[0].strip(), parts[1].strip()
                else:
                    speaker, content = "Unknown", text

                if content:
                    new_entries.append({"speaker": speaker, "text": content})

        except Exception as exc:
            logger.debug("Caption polling error: %s", exc)

        return new_entries

    async def send_chat_message(self, message: str) -> bool:
        """Send a message in the Meet chat.

        Args:
            message: Text to send.

        Returns:
            True if sent successfully.
        """
        if not self._page or not self._running:
            return False

        try:
            chat_selectors = [
                'button[aria-label="Chat with everyone"]',
                'button[aria-label="Chat"]',
                'button:has-text("Chat")',
            ]
            for selector in chat_selectors:
                try:
                    btn = await self._page.query_selector(selector)
                    if btn:
                        await btn.click()
                        await asyncio.sleep(1)
                        break
                except Exception:
                    continue

            input_selectors = [
                'textarea[aria-label="Send a message to everyone"]',
                'textarea[aria-label="Send a message"]',
                'textarea',
            ]
            for selector in input_selectors:
                try:
                    text_input = await self._page.wait_for_selector(selector, timeout=3000)
                    if text_input:
                        await text_input.type(message, delay=20)
                        await self._page.keyboard.press("Enter")
                        logger.info("Chat message sent: %s", message[:80])
                        return True
                except Exception:
                    continue

            logger.warning("Could not find chat input")
            return False

        except Exception as exc:
            logger.error("Failed to send chat message: %s", exc)
            return False

    async def leave(self) -> None:
        """Leave the meeting and close the browser."""
        self._running = False

        if self._poll_task:
            self._poll_task.cancel()

        if self._page:
            try:
                leave_btn = await self._page.query_selector(
                    'button[aria-label="Leave call"]'
                )
                if leave_btn:
                    await leave_btn.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        logger.info("Left meeting and closed browser")
