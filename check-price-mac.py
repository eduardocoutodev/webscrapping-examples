# Run selenium and chrome driver to scrape data from cloudbytes.dev
# Source: https://cloudbytes.dev/snippets/run-selenium-and-chrome-on-wsl2
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
import chromedriver_autoinstaller
import requests
from dotenv import load_dotenv
import os
import json
from selenium.common.exceptions import NoSuchElementException
import re

macbook_urls = [
    'https://www.apple.com/shop/product/G11C2LL/A/refurbished-133-inch-macbook-pro-apple-m1-chip-with-8‑core-cpu-and-8‑core-gpu-space-gray', 'https://www.apple.com/shop/product/G11B0LL/A/refurbished-133-inch-macbook-pro-apple-m1-chip-with-8‑core-cpu-and-8‑core-gpu-space-gray', 'https://www.apple.com/ie/shop/product/G11C3B/A/refurbished-133-inch-macbook-pro-apple-m1-chip-with-8‑core-cpu-and-8‑core-gpu-space-grey','https://www.apple.com/shop/product/FYD82LL/A/refurbished-133-inch-macbook-pro-apple-m1-chip-with-8‑core-cpu-and-8‑core-gpu-space-gray']

load_dotenv()

NTFY_TOPIC_NAME = os.getenv('NTFY_TOPIC')
NFTY_EMAIL_TO_SEND = os.getenv("NFTY_EMAIL_TO_SEND")


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
        # Give 5 seconds interval in between
        time.sleep(5)

    browser.quit()


def access_url(url: str, browser):
    try:
        print(f"Started Url: {url}")
        browser.get(url)

        time.sleep(2)

        # Get the current price of macbook
        current_price = extractPrices(browser.find_element(
            By.CSS_SELECTOR, ".rc-prices-fullprice").text)
        
        overview_pannel_container = browser.find_element(By.CSS_SELECTOR, ".Overview-panel")

        ram_memory = overview_pannel_container.find_element(
            By.XPATH, '//p[contains(.,"unified memory")]').text
        
        ssd_memory = overview_pannel_container.find_element(
            By.XPATH, '//p[contains(.,"SSD")]').text
        
        try:
            previous_price = extractPrices(browser.find_element(
                By.CSS_SELECTOR, ".rc-prices-currentprice .as-price-previousprice"
            ).text)

            savings = extractPrices(browser.find_element(
                By.CSS_SELECTOR, ".rc-prices-currentprice .rc-prices-savings"
            ).text)

            discountPercentage = round(
                float(savings) / float(previous_price), 2) * 100

            message_to_send = f"""Macbook with {ram_memory}, {ssd_memory}.
            Current Price: {current_price}
            Old Price: {previous_price}
            Saving: {savings}
            
            Saving percentage: {discountPercentage}%
            Link: {url}"""

            title = f"Macbook with discount of {discountPercentage}%"
            print(message_to_send)

            sendMessageToNFTYTopic(title, message_to_send)

        except NoSuchElementException:
            print("Previous Price not found, no discount, Skipping")

    except Exception as e:
        print(e)
        sendMessageToNFTYTopic("Error happened on Mac Script", e)


def extractPrices(text: str) -> str:
    match = re.search(r'[0-9,]+.[0-9]', text)

    if match:
        # Need to remove ',' from prices, need to parse 3,099.0 to 3099.0
        return match.group().replace(",", "")

    return ""


def sendMessageToNFTYTopic(title: str, message_to_send: str, ):
    body = json.dumps({
        "topic": NTFY_TOPIC_NAME,
        "message": message_to_send,
        "title": title,
    })

    headers = {
        "Email": NFTY_EMAIL_TO_SEND
    }

    requests.post("https://ntfy.sh/", data=body, headers=headers)
    print("Sent Request to NTFY with success")


if __name__ == "__main__":
    main()
