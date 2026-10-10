import functools
import json
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from ..conftest import RUNS

FRONTEND = Path(__file__).resolve().parents[3] / "frontend"
PASSWORD = "test-pass-123"


class _SinglePageApp(SimpleHTTPRequestHandler):
    def send_head(self):
        if not Path(self.translate_path(self.path)).exists():
            self.path = "/index.html"
        return super().send_head()

    def log_message(self, *_args):
        pass


@pytest.fixture(scope="session")
def console_url(live_api, tmp_path_factory):
    dist = tmp_path_factory.mktemp("console")
    env = {
        **os.environ,
        "VITE_API_BASE_URL": live_api.url,
        "VITE_SELF_HOSTED": "true",
        "VITE_CLERK_PUBLISHABLE_KEY": "",
        "VITE_PUBLIC_POSTHOG_KEY": "",
    }
    subprocess.run(
        ["bun", "run", "build", "--outDir", str(dist), "--emptyOutDir"],
        cwd=FRONTEND,
        env=env,
        check=True,
        capture_output=True,
        timeout=300,
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_SinglePageApp, directory=str(dist))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


class Browser:
    """Playwright's sync API runs an event loop on its thread; keeping it on its
    own thread leaves the test thread free for asyncio.run (MCP) and the ORM."""

    def __init__(self) -> None:
        self._thread = ThreadPoolExecutor(max_workers=1)
        self._playwright = self.call(lambda: sync_playwright().start())
        self._browser = self.call(self._playwright.chromium.launch)

    def call(self, fn, *args):
        return self._thread.submit(fn, *args).result()

    def page(self, base_url: str) -> "Console":
        page = self.call(lambda: self._browser.new_page(base_url=base_url))
        self.call(page.set_default_timeout, 20_000)
        return Console(self, page)

    def close(self) -> None:
        self.call(self._browser.close)
        self.call(self._playwright.stop)
        self._thread.shutdown()


class Console:
    def __init__(self, browser: Browser, page) -> None:
        self._browser, self._page = browser, page
        self.page_errors = []
        self.server_errors = []
        browser.call(page.on, "pageerror", lambda error: self.page_errors.append(str(error)))
        browser.call(
            page.on,
            "response",
            lambda response: (
                self.server_errors.append({"status": response.status, "url": response.url})
                if response.status >= 500
                else None
            ),
        )

    def goto(self, path: str) -> None:
        self._browser.call(self._page.goto, path)

    def fill(self, selector: str, value: str) -> None:
        self._browser.call(self._page.fill, selector, value)

    def press(self, button: str) -> None:
        self._browser.call(lambda: self._page.get_by_role("button", name=button).first.click())

    def click(self, text: str) -> None:
        self._browser.call(lambda: self._page.get_by_text(text, exact=True).first.click())

    def choose(self, trigger: str, option: str) -> None:
        self._browser.call(self._page.click, trigger)
        self._browser.call(lambda: self._page.get_by_role("option", name=option).click())

    def sees(self, text: str, *, exact: bool = True) -> None:
        self._browser.call(lambda: self._page.get_by_text(text, exact=exact).first.wait_for())

    def upload(self, selector: str, path: Path) -> None:
        self._browser.call(self._page.set_input_files, selector, str(path))

    def signed_in(self) -> bool:
        return bool(self._browser.call(self._page.evaluate, "localStorage.getItem('jwt_access')"))

    def wait_until(self, script: str) -> None:
        self._browser.call(self._page.wait_for_function, script)

    def save_evidence(self, name: str) -> None:
        RUNS.mkdir(parents=True, exist_ok=True)
        self._browser.call(
            lambda: self._page.screenshot(path=str(RUNS / f"{name}.png"), full_page=True)
        )
        facts = self._browser.call(
            self._page.evaluate,
            """() => ({url: location.href, text: document.body.innerText,
                colorScheme: getComputedStyle(document.documentElement).colorScheme})""",
        )
        facts.update(page_errors=self.page_errors, server_errors=self.server_errors)
        (RUNS / f"{name}.browser.json").write_text(json.dumps(facts, indent=2))

    def healthy(self) -> None:
        assert not self.page_errors, self.page_errors
        assert not self.server_errors, self.server_errors
        self.wait_until("() => getComputedStyle(document.documentElement).colorScheme === 'dark'")

    def close(self) -> None:
        self._browser.call(self._page.close)


@pytest.fixture(scope="session")
def browser():
    browser = Browser()
    yield browser
    browser.close()


@pytest.fixture
def console(browser, console_url, account, settings, request) -> Console:
    settings.CORS_ALLOW_ALL_ORIGINS = True
    user, _key = account
    console = browser.page(console_url)
    console.goto("/login")
    console.fill("#local-email", user.email)
    console.fill("#local-password", PASSWORD)
    console.press("Continue")
    console.wait_until("() => !!localStorage.getItem('jwt_access')")
    console.goto("/onboarding")
    console.click("Go to console")
    yield console
    try:
        console.save_evidence(re.sub(r"[^\w.-]+", "_", request.node.nodeid))
        console.healthy()
    finally:
        console.close()
