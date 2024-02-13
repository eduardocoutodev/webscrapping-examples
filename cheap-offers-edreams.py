from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    
    page.goto("https://www.edreams.pt/")
    page.get_by_role("button", name="Continuar sem aceitar →").click()
    page.(".css-u5q0l3 > .css-hnoulf > .css-1w7nvl9 > .css-8tn0p7 > .css-1upemms").first.click()

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
