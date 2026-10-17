from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8000/"


def main() -> None:
    errors: list[str] = []
    console_errors: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except Exception:
            browser = p.chromium.launch(
                executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                headless=True,
            )
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector(".hero-empty h1", timeout=20000)
        page.select_option("#connection-select", "demo-duckdb")
        page.wait_for_timeout(1000)
        print("connection:", page.input_value("#connection-select"))
        print("hero:", page.inner_text(".hero-empty h1"))

        page.fill("#question-input", "最近6个月每月的销售额趋势")
        page.click("#send-btn")
        page.wait_for_selector(".loading-card", timeout=10000)
        page.wait_for_selector(".assistant-card .card-title", timeout=120000)
        page.wait_for_timeout(2500)
        title = page.inner_text(".assistant-card .card-title")
        print("answer title:", title)
        print("has canvas:", page.locator("[data-chart-msg] canvas").count())
        print("answer text:", page.inner_text(".assistant-card .card-body")[:500].replace("\n", " | "))
        page.screenshot(path=r"C:\study\ai-sql\ui-answer.png")

        # Chart switch
        page.click('[data-chart-switch="bar"]')
        page.wait_for_timeout(1200)
        print("after bar switch, canvas:", page.locator("[data-chart-msg] canvas").count())
        page.screenshot(path=r"C:\study\ai-sql\ui-answer-bar.png")

        # Save to report
        page.click('[data-action="save-report"]')
        page.wait_for_timeout(1500)
        print("toast:", page.inner_text("#toast-root")[:200])

        # Reports tab
        page.click('.tab[data-view="reports"]')
        page.wait_for_timeout(1500)
        page.screenshot(path=r"C:\study\ai-sql\ui-reports.png")
        print("reports list:", page.inner_text("#reports-list")[:300].replace("\n", " | "))
        if page.locator(".report-detail h1").count():
            print("report title:", page.inner_text(".report-detail h1"))

        # Dark theme on analysis tab
        page.click('.tab[data-view="analysis"]')
        page.click("#theme-toggle")
        page.wait_for_timeout(1200)
        page.screenshot(path=r"C:\study\ai-sql\ui-dark.png")
        print("theme:", page.get_attribute("html", "data-theme"))

        page.close()
        browser.close()

    print("page errors:", errors)
    print("console errors:", console_errors[:10])
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
