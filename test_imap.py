import imaplib
import os
from dotenv import load_dotenv

load_dotenv()
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

imap = imaplib.IMAP4_SSL("imap.gmail.com")
imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
status, folders = imap.list()
print("Folders:")
for f in folders:
    print(f.decode("utf-8"))
imap.logout()
