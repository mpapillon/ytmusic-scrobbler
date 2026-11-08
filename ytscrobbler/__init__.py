from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

BROWSER_JSON_PATH = "browser.json"
DATA_JSON_PATH = "data.json"
MAX_BROWSER_SETUP_ATTEMPTS = 3
