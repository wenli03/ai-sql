from __future__ import annotations

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8000/"
SHOTS = r"C:\study\ai-sql\docs\screenshots"


def main() -> None:
    errors: list[str] = []
    console_errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector(".hero-empty h1", timeout=20000)
        page.wait_for_timeout(1000)
        page.screenshot(path=rf"{SHOTS}\home.png")

        page.select_option("#connection-select", "demo-duckdb")
        page.wait_for_timeout(1200)
        page.fill("#question-input", "各品类销售额排行")
        page.click("#send-btn")
        page.wait_for_selector(".assistant-card .card-title", timeout=120000)
        page.wait_for_timeout(2000)
        print("answer title:", page.inner_text(".assistant-card .card-title"))
        print("reliability:", page.inner_text(".assistant-card .reliability"))
        print("provenance button:", page.locator('[data-action="provenance"]').count())
        page.screenshot(path=rf"{SHOTS}\answer.png")

        page.click('[data-action="save-report"]')
        page.wait_for_timeout(1800)
        page.click('.tab[data-view="reports"]')
        page.wait_for_timeout(2000)
        if page.locator(".report-detail h1").count():
            print("report:", page.inner_text(".report-detail h1"))
            if page.locator(".report-detail details.prov > summary").count():
                page.click(".report-detail details.prov > summary")
                page.wait_for_timeout(800)
            page.screenshot(path=rf"{SHOTS}\reports.png")

        page.click('.tab[data-view="analysis"]')
        page.click("#theme-toggle")
        page.wait_for_timeout(1200)
        page.screenshot(path=rf"{SHOTS}\dark.png")
        browser.close()
    print("page errors:", errors)
    print("console errors:", console_errors[:10])
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
