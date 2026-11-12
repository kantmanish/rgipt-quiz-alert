import os
import sys
import time
import subprocess
from datetime import datetime, timedelta, timezone
import requests

BASE_URL = "https://rgipterp.in"
LOGIN_ID = os.getenv("LMS_USER", "24CS2025")
PASSWORD = os.getenv("LMS_PASSWORD", "YOUR_PASSWORD")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "rgipt_quiz_24cs2025_secret")
CHECK_INTERVAL = 15                       # Check every 15 seconds during class
MP3_FILE = "alarm.mp3"                      # Optional local alarm.mp3 in the same folder

# Class schedule settings (Indian Standard Time)
IST = timezone(timedelta(hours=5, minutes=30))
START_HOUR = 10   # 10:00 AM IST
END_HOUR = 19     # 7:00 PM IST (19:00)

def is_class_time():
    """Returns True only on Monday-Friday (0-4) between 10:00 AM and 6:00 PM IST."""
    now = datetime.now(IST)
    is_weekday = 0 <= now.weekday() <= 4  # 0 = Monday, 4 = Friday, 5 = Saturday, 6 = Sunday
    is_within_hours = START_HOUR <= now.hour < END_HOUR
    return is_weekday and is_within_hours

def next_class_start_str():
    """Returns a readable string of when the next 10:00 AM class window starts."""
    now = datetime.now(IST)
    target = now.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
    if now.weekday() <= 4 and now.hour < START_HOUR:
        return target.strftime("%A at %I:%M %p")
    days_ahead = 1
    while (now + timedelta(days=days_ahead)).weekday() > 4:
        days_ahead += 1
    next_day = (now + timedelta(days=days_ahead)).replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
    return next_day.strftime("%A at %I:%M %p")

def play_custom_mp3():
    if not os.path.exists(MP3_FILE):
        return
    try:
        if sys.platform == "win32":
            import pygame
            pygame.mixer.init()
            pygame.mixer.music.load(MP3_FILE)
            pygame.mixer.music.play()
        else:
            subprocess.Popen(
                ["mpv", "--no-video", MP3_FILE],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
    except Exception as e:
        print("Local MP3 playback skipped:", e)

def send_notification(title, message, alarm_level="info"):
    now_str = datetime.now(IST).strftime("%H:%M:%S")
    print(f"\n[{now_str}] [{alarm_level.upper()}] {title}\n -> {message}")

    priority_map = {"info": "default", "published": "high", "started": "max"}
    tags_map = {
        "info": "white_check_mark",
        "published": "mega,calendar",
        "started": "rotating_light,warning,alarm_clock"
    }

    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority_map.get(alarm_level, "high"),
                "Tags": tags_map.get(alarm_level, "bell")
            },
            timeout=5
        )
    except Exception as e:
        print("Phone notification error:", e)

    if alarm_level in ("published", "started"):
        play_custom_mp3()

def get_quiz_state(q):
    status = str(q.get("status") or "").lower()
    attempt_status = str(q.get("myAttemptStatus") or "").lower()
    actual_start = q.get("actualStartTime")
    actual_stop = q.get("actualStopTime")

    if (
        status in ("completed", "ended", "submitted", "closed")
        or attempt_status in ("submitted", "completed")
        or actual_stop is not None
    ):
        return "ENDED"

    if (
        actual_start is not None
        or status in ("live", "active", "started", "in_progress", "ongoing")
    ):
        return "STARTED"

    return "PUBLISHED"

def login(session):
    payload = {
        "identifier": LOGIN_ID,
        "username": LOGIN_ID,
        "rollNo": LOGIN_ID,
        "email": LOGIN_ID,
        "password": PASSWORD
    }
    res = session.post(f"{BASE_URL}/api/auth/student/login", json=payload, timeout=10)
    res.raise_for_status()
    token = res.json()["data"]["accessToken"]
    session.headers.update({"Authorization": f"Bearer {token}"})
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Logged in successfully.")
    return time.time()

def start_monitoring():
    session = requests.Session()
    last_login = 0
    known_quizzes = {}
    first_run = True
    was_sleeping = False

    send_notification(
        "RGIPT Quiz Monitor Active",
        f"Scheduled for Mon-Fri {START_HOUR}:00 to {END_HOUR}:00 IST ({LOGIN_ID}).",
        alarm_level="info"
    )

    while True:
        # Pause checking outside Monday-Friday 10:00 AM - 6:00 PM IST
        if not is_class_time():
            if not was_sleeping:
                now_str = datetime.now(IST).strftime("%a %I:%M %p")
                print(f"[{now_str}] Outside class hours. Sleeping until {next_class_start_str()}...")
                was_sleeping = True
            time.sleep(60)
            continue

        # Waking up at 10:00 AM (or starting during class hours)
        if was_sleeping:
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Class hours started! Resuming quiz checks...")
            was_sleeping = False
            last_login = 0  # Force a fresh login

        try:
            # Login if needed or refresh every 12 minutes before the 15-min token expires
            if time.time() - last_login > 720:
                last_login = login(session)

            res = session.get(f"{BASE_URL}/api/student/quizzes", timeout=10)
            if res.status_code == 401:
                last_login = login(session)
                continue

            res.raise_for_status()
            items = res.json().get("data", {}).get("items", [])

            for q in items:
                q_id = q.get("id")
                title = q.get("title", "Untitled Quiz")
                course_code = q.get("courseCode", "")
                course_name = q.get("courseName", "Course")
                duration = q.get("durationMinutes", "?")
                marks = q.get("totalMarks", "?")
                state = get_quiz_state(q)
                course_label = f"{course_code} ({course_name})".strip()

                if first_run:
                    known_quizzes[q_id] = state
                    if state == "PUBLISHED":
                        send_notification(
                            f"Quiz Published (Waiting): {course_code}",
                            f"'{title}' is published ({duration}m, {marks} marks) but NOT started yet.",
                            alarm_level="published"
                        )
                    elif state == "STARTED":
                        send_notification(
                            f"QUIZ IS LIVE NOW: {course_code}",
                            f"'{title}' ({duration}m, {marks} marks) is ALREADY STARTED!",
                            alarm_level="started"
                        )
                else:
                    if q_id not in known_quizzes:
                        known_quizzes[q_id] = state
                        if state == "PUBLISHED":
                            send_notification(
                                f"NEW QUIZ PUBLISHED: {course_code}",
                                f"{course_label}: '{title}' ({duration}m, {marks} marks) is published (Not started yet).",
                                alarm_level="published"
                            )
                        elif state == "STARTED":
                            send_notification(
                                f"QUIZ PUBLISHED & STARTED: {course_code}",
                                f"{course_label}: '{title}' ({duration}m, {marks} marks) just went LIVE immediately!",
                                alarm_level="started"
                            )
                    elif known_quizzes[q_id] != state:
                        old_state = known_quizzes[q_id]
                        known_quizzes[q_id] = state
                        if state == "STARTED":
                            send_notification(
                                f"QUIZ STARTED NOW: {course_code}",
                                f"Button is now clickable! {course_label}: '{title}' ({duration}m, {marks} marks) has STARTED!",
                                alarm_level="started"
                            )
                        elif state == "ENDED" and old_state != "ENDED":
                            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Quiz '{title}' has ended.")

            if first_run:
                print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Indexed {len(known_quizzes)} quizzes. Watching...")
                first_run = False
            else:
                print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Checked ({len(items)} quizzes) - No change.")

        except Exception as e:
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Check error: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    start_monitoring()