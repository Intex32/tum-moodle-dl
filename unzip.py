import os
import zipfile
import shutil


def extract(file, course_dir, policy):
    print("extracting...")
    if not os.path.exists(course_dir):
        os.makedirs(course_dir)
    if policy == "replace":
        shutil.rmtree(course_dir)
        os.mkdir(course_dir)
    else:
        print("unknown update policy")
        return
    with zipfile.ZipFile(file, 'r') as zip_ref:
        zip_ref.extractall(course_dir)
    os.remove(file)
