# Run selenium and chrome driver to scrape data from cloudbytes.dev
# Source: https://cloudbytes.dev/snippets/run-selenium-and-chrome-on-wsl2
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
import chromedriver_autoinstaller

macbook_urls = [
    'https://www.apple.com/shop/product/G11C2LL/A/refurbished-133-inch-macbook-pro-apple-m1-chip-with-8‑core-cpu-and-8‑core-gpu-space-gray']


def main():
    # Automatically download and install the appropriate version of ChromeDriver
    chromedriver_autoinstaller.install()

    # Set Chrome options (headless means the browser won't open)
    options = webdriver.ChromeOptions()
    # Uncomment this line if you don't want the browser to be visible
    options.add_argument('--headless')
    options.add_argument("--window-size=1920,1080")

    # Set the User Agent to bypass some website protections
    user_agent = 'Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.2 (KHTML, like Gecko) Chrome/22.0.1216.0 Safari/537.2'
    options.add_argument(f'user-agent={user_agent}')

    # Required for running in a Docker container
    options.add_argument('--no-sandbox')
    # Required for running in a Docker container
    options.add_argument('--disable-dev-shm-usage')

    browser = webdriver.Chrome(options=options)

    for url in macbook_urls:
        access_url(url, browser)


def access_url(url, browser):
    try:
        browser.get(url)

        time.sleep(5)

        # Get the current price of macbook
        current_price = browser.find_element(
            By.CSS_SELECTOR, ".rc-prices-fullprice").text

        print(f"Current Price is {current_price}")

    except Exception as e:
        print(e)
    finally:
        browser.quit()


if __name__ == "__main__":
    main()
