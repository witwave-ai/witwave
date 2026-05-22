"""Playwright-backed AsyncComputer implementation for ComputerTool."""

import asyncio
import base64
import contextlib
import logging
import os
import time

from agents.computer import AsyncComputer, Button

logger = logging.getLogger(__name__)

# #1053: idle-release sweep + hard concurrent-context cap. Without these
# the pool held one Chromium BrowserContext per session_id for the full
# LRU lifetime (#522 scoping change), which on bursty multi-session
# traffic could grow RSS linearly and OOM the pod. Defaults:
#   BROWSER_CONTEXT_MAX_IDLE_SECONDS (600s / 10min) — close a context
#     whose last acquire/use is older than this.
#   COMPUTER_MAX_CONTEXTS (32) — hard cap on concurrent live contexts;
#     ``acquire`` evicts the oldest-idle entry on overflow.
#   BROWSER_CONTEXT_SWEEP_INTERVAL_SECONDS (60s) — sweeper cadence.
BROWSER_CONTEXT_MAX_IDLE_SECONDS = float(os.environ.get("BROWSER_CONTEXT_MAX_IDLE_SECONDS", "600"))
COMPUTER_MAX_CONTEXTS = int(os.environ.get("COMPUTER_MAX_CONTEXTS", "32"))
BROWSER_CONTEXT_SWEEP_INTERVAL_SECONDS = float(os.environ.get("BROWSER_CONTEXT_SWEEP_INTERVAL_SECONDS", "60"))

# #1619: Chromium sandbox posture is opt-out-of-sandbox, not the historical
# always-disabled. The previous unconditional ``--no-sandbox`` +
# ``--disable-setuid-sandbox`` made Chromium's renderer/zygote escapes a
# single bug away from full pod compromise, even on hosts whose kernels
# (user namespaces, seccomp) are perfectly capable of enforcing the
# sandbox. Set ``CHROMIUM_SANDBOX_DISABLED=true`` only when the runtime
# genuinely cannot host the sandbox (e.g. some unprivileged-container
# combos, kernels without unprivileged user namespaces). Default off so
# production benefits from defense-in-depth automatically.
# ``--disable-dev-shm-usage`` stays on unconditionally — it is a memory
# workaround for tiny ``/dev/shm`` mounts in containers, not a security
# toggle.
_CHROMIUM_SANDBOX_DISABLED = os.environ.get("CHROMIUM_SANDBOX_DISABLED", "false").lower() in ("true", "1", "yes")


def _chromium_launch_args() -> list[str]:
    """Return the Chromium launch flag list honouring the sandbox opt-out."""
    args = ["--disable-dev-shm-usage"]
    if _CHROMIUM_SANDBOX_DISABLED:
        # Insert sandbox-disabling flags ahead of the memory flag so the
        # ordering matches the historical args list and is easy to grep.
        args = ["--no-sandbox", "--disable-setuid-sandbox", *args]
    return args


logger.info(
    "Chromium sandbox posture: %s (CHROMIUM_SANDBOX_DISABLED=%s)",
    "DISABLED (--no-sandbox active)" if _CHROMIUM_SANDBOX_DISABLED else "ENABLED (host kernel sandbox)",
    "true" if _CHROMIUM_SANDBOX_DISABLED else "false",
)

_BUTTON_MAP: dict[str, str] = {
    "left": "left",
    "right": "right",
    "wheel": "middle",
}


class PlaywrightComputer(AsyncComputer):
    """AsyncComputer backed by a headless Playwright Chromium browser.

    Two usage modes are supported:

    1. **Stand-alone mode** (``__init__`` with no ``pool``): the instance
       manages its own Playwright process, browser, context, and page. This
       preserves the original single-session behaviour and is used for
       simple/test paths.

    2. **Pool-scoped mode** (``__init__`` with a ``BrowserPool`` and
       ``session_id``): the instance shares the pool's long-lived browser
       process but owns a fresh ``BrowserContext`` + ``Page`` that are
       isolated from every other session (cookies, localStorage,
       service workers, cache, IndexedDB). Call ``close()`` to release the
       context; the pool's browser stays up for the process lifetime.

    Either way, ``_op_lock`` serialises page operations for one computer
    instance. Per-session instances do not share a lock — the Agents SDK
    handles one session at a time per ComputerTool binding.
    """

    def __init__(
        self,
        width: int = 1280,
        height: int = 800,
        *,
        pool: "BrowserPool | None" = None,
        session_id: str | None = None,
    ) -> None:
        self._width = width
        self._height = height
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._pool = pool
        self._session_id = session_id
        self._lock = asyncio.Lock()
        # Serializes page operations across concurrent callers.  All public
        # methods that interact with self._page must be called while holding
        # this lock so that concurrent sessions do not interleave page actions.
        self._op_lock = asyncio.Lock()

    @property
    def environment(self):
        return "browser"

    @property
    def dimensions(self) -> tuple[int, int]:
        return (self._width, self._height)

    async def _ensure_page(self):
        async with self._lock:
            if self._page is not None:
                return
            if self._pool is not None:
                # Pool-scoped: borrow the shared browser, open a private
                # context + page for this session.
                browser = await self._pool._ensure_browser()
                self._context = await browser.new_context(
                    viewport={"width": self._width, "height": self._height},
                )
                self._page = await self._context.new_page()
                logger.info(
                    "Playwright per-session context opened for session %r (%dx%d)",
                    self._session_id,
                    self._width,
                    self._height,
                )
                return
            # Stand-alone: launch an independent browser process.
            from playwright.async_api import async_playwright

            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=_chromium_launch_args(),
                )
                self._context = await self._browser.new_context(
                    viewport={"width": self._width, "height": self._height},
                )
                self._page = await self._context.new_page()
                logger.info("Playwright browser started (%dx%d)", self._width, self._height)
            except Exception:
                # Clean up any partially-initialized resources so the next call
                # can retry from a clean state without leaking Playwright processes.
                await self.close()
                raise

    async def screenshot(self) -> str:
        async with self._op_lock:
            await self._ensure_page()
            png = await self._page.screenshot(type="png")
            return base64.b64encode(png).decode()

    async def click(self, x: int, y: int, button: Button) -> None:
        async with self._op_lock:
            await self._ensure_page()
            if button == "back":
                await self._page.go_back()
                return
            if button == "forward":
                await self._page.go_forward()
                return
            pw_button = _BUTTON_MAP.get(button, "left")
            await self._page.mouse.click(x, y, button=pw_button)

    async def double_click(self, x: int, y: int) -> None:
        async with self._op_lock:
            await self._ensure_page()
            await self._page.mouse.dblclick(x, y)

    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
        async with self._op_lock:
            await self._ensure_page()
            await self._page.mouse.move(x, y)
            await self._page.evaluate(f"window.scrollBy({scroll_x}, {scroll_y})")

    async def type(self, text: str) -> None:
        async with self._op_lock:
            await self._ensure_page()
            await self._page.keyboard.type(text)

    async def wait(self) -> None:
        await asyncio.sleep(1.0)

    async def move(self, x: int, y: int) -> None:
        async with self._op_lock:
            await self._ensure_page()
            await self._page.mouse.move(x, y)

    async def keypress(self, keys: list[str]) -> None:
        async with self._op_lock:
            await self._ensure_page()
            for key in keys:
                await self._page.keyboard.press(key)

    async def drag(self, path: list[tuple[int, int]]) -> None:
        async with self._op_lock:
            await self._ensure_page()
            if not path:
                return
            await self._page.mouse.move(path[0][0], path[0][1])
            await self._page.mouse.down()
            for x, y in path[1:]:
                await self._page.mouse.move(x, y)
            await self._page.mouse.up()

    async def close(self) -> None:
        if self._context:
            try:
                await self._context.close()
            except Exception as _e:
                logger.warning("Error closing Playwright context: %s", _e)
            self._context = None
            self._page = None
        # Only tear down the browser + playwright process if this instance
        # owns them (stand-alone mode). Pool-scoped instances leave the
        # shared browser alone so other sessions can keep using it.
        if self._pool is None:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception as _e:
                    logger.warning("Error closing Playwright browser: %s", _e)
                self._browser = None
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as _e:
                    logger.warning("Error stopping Playwright: %s", _e)
                self._playwright = None
            logger.info("Playwright browser closed")
        else:
            logger.info(
                "Playwright per-session context closed for session %r",
                self._session_id,
            )


class BrowserPool:
    """Shared Playwright browser process with per-session context scoping.

    One Chromium process is launched lazily on first use and reused for the
    lifetime of the pool. Each call to ``acquire(session_id)`` returns a
    ``PlaywrightComputer`` backed by a fresh ``BrowserContext`` — so
    cookies, localStorage, service workers, cache, IndexedDB, and page
    state are isolated between sessions.

    Call ``release(session_id)`` (or ``close()`` for the whole pool) to
    close the per-session context. The shared browser keeps running until
    ``close()`` is invoked.

    Addresses #522: the previous module-global ``PlaywrightComputer``
    singleton shared one browser context across every A2A session, which
    leaked authenticated state and page contents between trust boundaries.
    """

    def __init__(self, width: int = 1280, height: int = 800) -> None:
        self._width = width
        self._height = height
        self._playwright = None
        self._browser = None
        self._browser_lock = asyncio.Lock()
        self._computers: dict[str, PlaywrightComputer] = {}
        self._computers_lock = asyncio.Lock()
        # #1053: last-touch timestamps keyed by session_id, in monotonic
        # seconds. Updated on every acquire() so active sessions never
        # reach the idle threshold mid-use.
        self._last_touch: dict[str, float] = {}
        self._sweeper_task: asyncio.Task | None = None
        self._sweeper_stop: asyncio.Event | None = None

    async def _ensure_browser(self):
        async with self._browser_lock:
            if self._browser is not None:
                return self._browser
            from playwright.async_api import async_playwright

            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=_chromium_launch_args(),
                )
                logger.info(
                    "Playwright shared browser started (%dx%d) for per-session contexts",
                    self._width,
                    self._height,
                )
                return self._browser
            except Exception:
                # Clean partial state so a retry can restart cleanly.
                if self._browser is not None:
                    try:
                        await self._browser.close()
                    except Exception:
                        pass
                    self._browser = None
                if self._playwright is not None:
                    try:
                        await self._playwright.stop()
                    except Exception:
                        pass
                    self._playwright = None
                raise

    async def acquire(self, session_id: str) -> PlaywrightComputer:
        """Return a session-scoped PlaywrightComputer.

        A single computer instance is cached per ``session_id`` for the
        lifetime of the session so consecutive tool calls within one
        session reuse their page/history (matching the semantics the
        original docstring claimed but did not enforce).
        """
        # #1053: enforce cap + record activity. Overflow evicts the
        # least-recently-touched entry; sweeper loop handles idle-release
        # independently.
        evicted: list[tuple[str, PlaywrightComputer]] = []
        async with self._computers_lock:
            existing = self._computers.get(session_id)
            if existing is not None:
                self._last_touch[session_id] = time.monotonic()
                return existing
            # Evict oldest-idle if we're at the cap so active sessions
            # don't get starved. We collect (sid, computer) tuples here
            # and close them outside the lock.
            if COMPUTER_MAX_CONTEXTS > 0 and len(self._computers) >= COMPUTER_MAX_CONTEXTS:
                ordered = sorted(
                    self._computers.keys(),
                    key=lambda sid: self._last_touch.get(sid, 0.0),
                )
                overflow = len(self._computers) - COMPUTER_MAX_CONTEXTS + 1
                for sid in ordered[:overflow]:
                    comp = self._computers.pop(sid, None)
                    self._last_touch.pop(sid, None)
                    if comp is not None:
                        evicted.append((sid, comp))
            computer = PlaywrightComputer(
                width=self._width,
                height=self._height,
                pool=self,
                session_id=session_id,
            )
            self._computers[session_id] = computer
            self._last_touch[session_id] = time.monotonic()
        # Close evicted computers outside the lock to avoid holding it
        # across playwright IPC.
        for sid, comp in evicted:
            logger.info(
                "BrowserPool evicting session %r to honour COMPUTER_MAX_CONTEXTS=%d",
                sid,
                COMPUTER_MAX_CONTEXTS,
            )
            try:
                await comp.close()
            except Exception as _e:
                logger.warning(
                    "Failed to close evicted PlaywrightComputer for %r: %s",
                    sid,
                    _e,
                )
        # Start sweeper lazily on first acquire so unused pools don't
        # spawn a dangling task.
        if self._sweeper_task is None and BROWSER_CONTEXT_MAX_IDLE_SECONDS > 0:
            self._sweeper_stop = asyncio.Event()
            self._sweeper_task = asyncio.create_task(self._idle_sweeper())
        return computer

    async def _idle_sweeper(self) -> None:
        """Periodically close contexts idle > BROWSER_CONTEXT_MAX_IDLE_SECONDS.

        Runs until ``close()`` sets ``_sweeper_stop``. Each pass takes a
        snapshot under the lock and performs the actual ``comp.close()``
        outside the lock to avoid holding it across Playwright IPC.
        """
        assert self._sweeper_stop is not None
        try:
            while not self._sweeper_stop.is_set():
                try:
                    await asyncio.wait_for(
                        self._sweeper_stop.wait(),
                        timeout=BROWSER_CONTEXT_SWEEP_INTERVAL_SECONDS,
                    )
                    return
                except asyncio.TimeoutError:
                    pass
                now = time.monotonic()
                stale: list[tuple[str, PlaywrightComputer]] = []
                async with self._computers_lock:
                    for sid, last in list(self._last_touch.items()):
                        if (now - last) >= BROWSER_CONTEXT_MAX_IDLE_SECONDS:
                            comp = self._computers.pop(sid, None)
                            self._last_touch.pop(sid, None)
                            if comp is not None:
                                stale.append((sid, comp))
                for sid, comp in stale:
                    logger.info(
                        "BrowserPool idle-release session %r (idle > %.0fs)",
                        sid,
                        BROWSER_CONTEXT_MAX_IDLE_SECONDS,
                    )
                    try:
                        await comp.close()
                    except Exception as _e:
                        logger.warning(
                            "Failed to close idle PlaywrightComputer for %r: %s",
                            sid,
                            _e,
                        )
        except asyncio.CancelledError:
            raise
        except Exception as _e:
            logger.warning("BrowserPool idle sweeper exited: %r", _e)

    async def release(self, session_id: str) -> None:
        """Close and drop the per-session context for ``session_id``.

        Safe to call for sessions that never acquired a computer.
        """
        async with self._computers_lock:
            computer = self._computers.pop(session_id, None)
            self._last_touch.pop(session_id, None)
        if computer is None:
            return
        try:
            await computer.close()
        except Exception as _e:
            logger.warning(
                "Failed to close per-session PlaywrightComputer for %r: %s",
                session_id,
                _e,
            )

    async def close(self) -> None:
        """Close every per-session context and the shared browser."""
        # #1053: stop the idle sweeper first so it doesn't race our close.
        if self._sweeper_stop is not None:
            self._sweeper_stop.set()
        if self._sweeper_task is not None:
            try:
                await asyncio.wait_for(self._sweeper_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # #1500: after cancel() we must await the task so it
                # actually observes the CancelledError and finishes its
                # teardown (close Playwright IPC, flush telemetry). Without
                # the await, asyncio reports "Task was destroyed but it is
                # pending" and Playwright child processes can linger.
                self._sweeper_task.cancel()
                with contextlib.suppress(BaseException):
                    await self._sweeper_task
            except Exception:
                pass
            self._sweeper_task = None
            self._sweeper_stop = None
        async with self._computers_lock:
            computers = list(self._computers.items())
            self._computers.clear()
            self._last_touch.clear()
        for session_id, computer in computers:
            try:
                await computer.close()
            except Exception as _e:
                logger.warning(
                    "Failed to close per-session PlaywrightComputer for %r on shutdown: %s",
                    session_id,
                    _e,
                )
        async with self._browser_lock:
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception as _e:
                    logger.warning("Error closing shared Playwright browser: %s", _e)
                self._browser = None
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception as _e:
                    logger.warning("Error stopping shared Playwright: %s", _e)
                self._playwright = None
        logger.info("Playwright shared browser pool closed")
