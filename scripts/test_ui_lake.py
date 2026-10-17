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

        # Dynamic connection form
        page.click("#add-connection")
        page.wait_for_selector("#connection-form", timeout=10000)
        options = page.locator("#source-type option").all_text_contents()
        print("source options:", options)
        page.select_option("#source-type", "trino")
        page.wait_for_timeout(300)
        print("trino fields:", page.locator("#source-fields .form-row label").all_text_contents())
        page.screenshot(path=rf"{SHOTS}\connection-modal.png")
        page.click(".modal-close")
        page.wait_for_timeout(300)

        # Switch to the local data lake
        page.select_option("#connection-select", "demo-duckdb")
        page.wait_for_timeout(1200)
        print("connection card:", page.inner_text("#connection-card").replace("\n", " | "))
        page.fill("#question-input", "最近6个月每月的销售额趋势")
        page.click("#send-btn")
        page.wait_for_selector(".assistant-card .card-title", timeout=120000)
        page.wait_for_timeout(1800)
        print("answer:", page.inner_text(".assistant-card .card-title"))
        print("reliability badge:", page.inner_text(".assistant-card .reliability"))
        page.click('[data-action="provenance"]')
        page.wait_for_selector("#provenance-body", timeout=10000)
        page.wait_for_timeout(500)
        body = page.inner_text("#provenance-body")
        print("provenance keys:", [key for key in ["来源映射", "字段级血缘", "可靠性检查", "数据新鲜度", "parquet"] if key in body])
        print("provenance excerpt:", body[:600].replace("\n", " | "))
        page.screenshot(path=rf"{SHOTS}\provenance.png")

        page.click("[data-refresh-freshness]")
        page.wait_for_timeout(2500)
        print("after refresh:", page.inner_text("#provenance-body")[:200].replace("\n", " | "))

        browser.close()
    print("page errors:", errors)
    print("console errors:", console_errors[:10])
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
