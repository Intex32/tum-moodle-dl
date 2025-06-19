# TU·M·oo·DL
**TUM moodle downloader** - a simple, selenium-based, script for downloading moodle courses at TUM (Technical University Munich) written in Python
# configuration
the following shows a sample configuration.
```json
{
    "username": "TODO",
    "password": "TODO",
    "headless": true,

    "courses": [
        {
            "id": 0,
            "originalFilename": false,
            "destination": "/home/user/Desktop/moodle-course/",
            "update_policy": "replace"
        }
    ]
}
```
