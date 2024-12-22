from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
import time
from selenium.webdriver.chrome.options import Options
import os

def download_wait(directory, timeout):
    seconds = 0
    dl_wait = True
    while dl_wait and seconds < timeout:
        time.sleep(1)
        dl_wait = False
        files = os.listdir(directory)
        for fname in files:
            if fname.endswith('.crdownload'):
                dl_wait = True
        seconds += 1
    return seconds

def find_newest_file(directory):
    files = [os.path.join(directory, f) for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
    latest_file = max(files, key=os.path.getctime)
    return latest_file

def download(absolute_download_dir, course_id, username, password, headless=True):
    options = Options()
    options.add_experimental_option("prefs", {
      "download.default_directory": absolute_download_dir,
      "download.prompt_for_download": False,
      "download.directory_upgrade": True,
      "safebrowsing.enabled": True
    })
    if headless:
        options.add_argument('--headless=new')
    driver = webdriver.Chrome(options)

    # load IDP login page
    driver.get("https://www.moodle.tum.de/Shibboleth.sso/Login?providerId=https%3A%2F%2Ftumidp.lrz.de%2Fidp%2Fshibboleth&target=https%3A%2F%2Fwww.moodle.tum.de%2Fauth%2Fshibboleth%2Findex.php")

    WebDriverWait(driver, 5).until(expected_conditions.presence_of_element_located((By.ID, 'username'))) # wait for page to load login mask
    username_element = driver.find_element(By.ID, "username")
    password_element = driver.find_element(By.ID, "password")

    # enter credentials
    username_element.clear()
    username_element.send_keys(username)
    password_element.clear()
    password_element.send_keys(password)
    password_element.send_keys(Keys.RETURN)

    time.sleep(3)

    # move to download page
    driver.get(f"https://www.moodle.tum.de/local/downloadcenter/index.php?courseid={course_id}")
    driver.find_element(By.ID, "id_filesrealnames").click()
    download_submit = driver.find_element(By.ID, "id_submitbutton")
    download_submit.click()

    print(f"started download {course_id}")

    time.sleep(3)
    download_wait(absolute_download_dir, 999)
    driver.close()

    return find_newest_file(absolute_download_dir)
