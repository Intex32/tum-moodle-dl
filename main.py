import os
from dl import *
from unzip import *
import json

def run(config):
  cwd = os.getcwd()
  absolute_download_dir = f"{cwd}/tmp"
  
  for course in config["courses"]:
    file = download(absolute_download_dir, course["id"], config["username"], config["password"], headless=config["headless"])
    extract(file, cwd, course["name"])

  os.rmdir(absolute_download_dir)

with open('config.json') as config_file:
  config = json.load(config_file)
run(config)

