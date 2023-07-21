# Run selenium and chrome driver to scrape data from cloudbytes.dev
# Source: https://cloudbytes.dev/snippets/run-selenium-and-chrome-on-wsl2
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
import chromedriver_autoinstaller


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

    url = "https://www.imdb.com/chart/top/"

    browser = webdriver.Chrome(options=options)

    try:
        browser.get(url)

        time.sleep(5)

        # Scroll to the bottom of the page to load all the data
        browser.execute_script(
            "window.scrollTo(0, document.body.scrollHeight);")

        time.sleep(5)

        # Get the list of movies
        list_titles = browser.find_elements(
            By.CSS_SELECTOR, ".ipc-metadata-list-summary-item .ipc-title__text")

        for title in list_titles:
            print(title.text)

    except Exception as e:
        print(e)
    finally:
        browser.quit()


if __name__ == "__main__":
    main()
