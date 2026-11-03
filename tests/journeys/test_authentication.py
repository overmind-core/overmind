import pytest

from overbae.models import Feedback

from .browser import PASSWORD

pytestmark = pytest.mark.covers("authentication")


@pytest.mark.covers("live-console")
def test_unavailable_page_offers_recovery_without_reloading(console):
    interrupted = []

    def interrupt(route):
        interrupted.append(route.request.url)
        route.abort()

    console._browser.call(console._page.route, "**/assets/inference-*.js", interrupt)
    console._browser.call(console._page.evaluate, "window.originalDocument = true")
    console._browser.call(
        lambda: console._page.get_by_role("link", name="Inference", exact=True).click()
    )
    console.sees("Page unavailable")
    assert interrupted
    console.wait_until("() => window.originalDocument === true")
    console.save_evidence("page-unavailable-controlled-recovery")
    console._browser.call(console._page.unroute, "**/assets/inference-*.js", interrupt)
    console.press("Refresh page")
    console.sees("No projects yet")
    console.wait_until("() => !window.originalDocument && location.pathname === '/inference'")
    console.save_evidence("page-recovered-after-explicit-refresh")


def test_expired_session_restores_without_losing_a_draft(console, account):
    user, _ = account
    console.press("Feedback")
    console.fill("#feedback-text", "Keep this unsent draft while I reconnect.")
    console._browser.call(
        console._page.evaluate,
        """() => {
            window.originalDocument = true;
            document.addEventListener('click', () => {
                localStorage.setItem('jwt_access', 'expired');
                localStorage.setItem('jwt_refresh', 'expired');
            }, {capture: true, once: true});
        }""",
    )
    console.press("Submit feedback")
    console.sees("Session interrupted")
    console.save_evidence("session-interrupted-draft-retained")
    console.fill("#local-password", PASSWORD)
    console.press("Restore session")
    console.wait_until("() => !document.body.innerText.includes('Session interrupted')")
    console.wait_until(
        "() => window.originalDocument && "
        "document.querySelector('#feedback-text')?.value === "
        "'Keep this unsent draft while I reconnect.'"
    )
    assert not Feedback.objects.filter(user=user).exists()
    console.save_evidence("session-restored-unsent-draft")


def test_sign_in_returns_to_a_deep_link_without_reloading(browser, console_url, account, settings):
    settings.CORS_ALLOWED_ORIGINS = [console_url]
    user, _ = account
    console = browser.page(console_url)
    try:
        console.goto("/inference?inf_search=hello%20world#results")
        console.sees("Creates an account on first use.")
        console.save_evidence("sign-in-deep-link-before")
        console._browser.call(console._page.evaluate, "window.originalDocument = true")
        console.fill("#local-email", user.email)
        console.fill("#local-password", PASSWORD)
        console.press("Continue")
        console.sees("No projects yet")
        console.save_evidence("sign-in-deep-link-arrival")
        console.wait_until(
            "() => window.originalDocument && location.pathname === '/inference' && "
            "new URLSearchParams(location.search).get('inf_search') === 'hello world' && "
            "location.hash === '#results'"
        )
        console.save_evidence("sign-in-deep-link-preserved")
        console.healthy()
    finally:
        console.close()
