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
        page.select_option("#connection-select", "demo-chroma")
        page.wait_for_timeout(1200)
        print("connection:", page.inner_text("#connection-card").replace("\n", " | "))
        page.fill("#question-input", "按 category 统计文档数量")
        page.click("#send-btn")
        page.wait_for_selector(".assistant-card .card-title", timeout=120000)
        page.wait_for_timeout(2000)
        print("title:", page.inner_text(".assistant-card .card-title"))
        print("reliability:", page.inner_text(".assistant-card .reliability"))
        print("plan label:", page.locator(".assistant-card details.sql summary").first.inner_text())
        page.click('[data-action="provenance"]')
        page.wait_for_selector("#provenance-body", timeout=10000)
        page.wait_for_timeout(500)
        body = page.inner_text("#provenance-body")
        print("provenance keys:", [k for k in ["来源映射", "字段级血缘", "knowledge_docs", "collection", "查看向量库请求"] if k in body])
        page.screenshot(path=rf"{SHOTS}\vector-provenance.png")
        page.click(".modal-close")
        page.wait_for_timeout(300)
        page.click("#new-analysis")
        page.fill("#question-input", "找与退款政策最相似的5条文档")
        page.click("#send-btn")
        page.wait_for_selector(".assistant-card .card-title", timeout=120000)
        page.wait_for_timeout(2000)
        print("search title:", page.inner_text(".assistant-card .card-title"))
        print("table first row:", page.locator(".assistant-card table.data tbody tr").first.inner_text().replace("\t", " | "))
        page.screenshot(path=rf"{SHOTS}\vector-search.png")
        browser.close()
    print("page errors:", errors)
    print("console errors:", console_errors[:10])
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
