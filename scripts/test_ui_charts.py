from __future__ import annotations

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8000/"
SHOTS = r"C:\study\ai-sql\docs\screenshots"


def ask(page, question: str, wait_selector: str, timeout: int = 120000) -> None:
    page.fill("#question-input", question)
    page.click("#send-btn")
    page.wait_for_selector(wait_selector, timeout=timeout)
    page.wait_for_timeout(1800)


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
        page.select_option("#connection-select", "demo-duckdb")
        page.wait_for_timeout(1000)

        ask(page, "总销售额是多少", ".kpi-value")
        print("KPI:", page.inner_text(".kpi-value"))
        print("KPI label:", page.inner_text(".kpi-label"))
        page.screenshot(path=rf"{SHOTS}\kpi.png")

        page.click("#new-analysis")
        ask(page, "各品类销售额占比", "[data-chart-msg] canvas")
        print("PIE canvas:", page.locator("[data-chart-msg] canvas").count())
        page.screenshot(path=rf"{SHOTS}\pie.png")

        page.click("#new-analysis")
        ask(page, "最近3个月各渠道的销售额趋势", "[data-chart-msg] canvas")
        print("LINE canvas:", page.locator("[data-chart-msg] canvas").count())
        page.screenshot(path=rf"{SHOTS}\multi-line.png")

        browser.close()
    print("page errors:", errors)
    print("console errors:", console_errors[:10])
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
