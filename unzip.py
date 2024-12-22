import os
import zipfile

def extract(file, path, course_name):
    print("extracting...")
    course_dir = f"{path}/{course_name}"
    os.mkdir(course_dir)
    with zipfile.ZipFile(file, 'r') as zip_ref:
        zip_ref.extractall(course_dir)
    os.remove(file)
