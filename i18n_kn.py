"""
i18n_kn.py — Kannada language pack for Vaani (used by i18n.py).

  * SENTENCES: Kannada for every fixed English sentence Vaani says
  * PHRASES / NUMBERS: Kannada booking words -> English for the rule readers
  * DAYS / MONTHS / PERIODS: how dates and times are spoken

Native-speaker review welcome: keep the {placeholders} unchanged.
"""

import re

KANNADA = re.compile(r"[ಀ-೿]")

SENTENCES = {
    "What date would you like to book the table for?": "ಯಾವ ದಿನಾಂಕಕ್ಕೆ ಟೇಬಲ್ ಬುಕ್ ಮಾಡಬೇಕು?",
    "What time works best for you?": "ನಿಮಗೆ ಯಾವ ಸಮಯ ಸರಿ ಹೋಗುತ್ತದೆ?",
    "How many guests will be joining?": "ಎಷ್ಟು ಜನ ಬರುತ್ತೀರಿ?",
    "What name should I put the reservation under?": "ಬುಕಿಂಗ್ ಯಾರ ಹೆಸರಿನಲ್ಲಿ ಮಾಡಲಿ?",
    "Could I get your 10-digit mobile number for the booking?": "ಬುಕಿಂಗ್‌ಗಾಗಿ ನಿಮ್ಮ 10 ಅಂಕಿಯ ಮೊಬೈಲ್ ನಂಬರ್ ಹೇಳಿ.",
    "What is the 10-digit mobile number registered for the booking?": "ಬುಕಿಂಗ್ ಯಾವ 10 ಅಂಕಿಯ ಮೊಬೈಲ್ ನಂಬರ್‌ನಲ್ಲಿ ಇದೆ?",
    "On which date was the reservation?": "ಬುಕಿಂಗ್ ಯಾವ ದಿನಾಂಕಕ್ಕೆ ಇತ್ತು?",
    "At what time was the reservation?": "ಬುಕಿಂಗ್ ಯಾವ ಸಮಯಕ್ಕೆ ಇತ್ತು?",
    "That date has already passed.": "ಆ ದಿನಾಂಕ ಈಗಾಗಲೇ ಕಳೆದಿದೆ.",
    "Could you choose a future date for the booking?": "ದಯವಿಟ್ಟು ಮುಂದಿನ ಯಾವುದಾದರೂ ದಿನಾಂಕ ಆಯ್ಕೆ ಮಾಡಿ.",
    "I want to be accurate, and I am not fully sure from our records.":
        "ನಾನು ಸರಿಯಾದ ಮಾಹಿತಿ ಕೊಡಲು ಬಯಸುತ್ತೇನೆ, ಆದರೆ ನಮ್ಮ ದಾಖಲೆಗಳಲ್ಲಿ ಇದರ ಬಗ್ಗೆ ಖಚಿತ ಮಾಹಿತಿ ಇಲ್ಲ.",
    "Please call us at {phone}.": "ದಯವಿಟ್ಟು {phone} ಗೆ ಕರೆ ಮಾಡಿ.",
    "For groups of more than {n} guests, we use our private dining room with at least 7 days advance notice.":
        "{n}ಕ್ಕಿಂತ ಹೆಚ್ಚು ಜನರಿಗೆ ನಮ್ಮ ಪ್ರೈವೇಟ್ ಡೈನಿಂಗ್ ರೂಮ್ ಇದೆ, ಅದಕ್ಕೆ ಕನಿಷ್ಠ 7 ದಿನ ಮುಂಚಿತವಾಗಿ ತಿಳಿಸಬೇಕು.",
    "Please call us at {phone} or email {email}.": "ದಯವಿಟ್ಟು {phone} ಗೆ ಕರೆ ಮಾಡಿ ಅಥವಾ {email} ಗೆ ಇಮೇಲ್ ಮಾಡಿ.",
    "Our largest table seats {n}.": "ನಮ್ಮ ಅತಿ ದೊಡ್ಡ ಟೇಬಲ್‌ನಲ್ಲಿ {n} ಜನ ಕೂರಬಹುದು.",
    "For bigger groups please call us at {phone}.": "ದೊಡ್ಡ ಗುಂಪುಗಳಿಗೆ ದಯವಿಟ್ಟು {phone} ಗೆ ಕರೆ ಮಾಡಿ.",
    "We have no free tables that day.": "ಆ ದಿನ ಯಾವುದೇ ಟೇಬಲ್ ಖಾಲಿ ಇಲ್ಲ.",
    "Would you like to try another date?": "ಬೇರೆ ದಿನಾಂಕ ನೋಡೋಣವೇ?",
    "I can offer {times}.": "{times} ಲಭ್ಯವಿದೆ.",
    "Which would you prefer?": "ನಿಮಗೆ ಯಾವುದು ಬೇಕು?",
    "Just to confirm: a table for {people} on {date} at {time}, under the name {name}, mobile ending {digits}.":
        "ಒಮ್ಮೆ ಖಚಿತಪಡಿಸುತ್ತೇನೆ: {date}, {time}, {people} ಜನರಿಗೆ ಟೇಬಲ್, ಹೆಸರು {name}, "
        "ಮೊಬೈಲ್ ನಂಬರ್‌ನ ಕೊನೆಯ ಅಂಕಿಗಳು {digits}.",
    "Shall I book it?": "ಬುಕ್ ಮಾಡಲೇ?",
    "Your table for {people} on {date} at {time} is confirmed under {name}.":
        "{name} ಹೆಸರಿನಲ್ಲಿ {date}, {time}, {people} ಜನರಿಗೆ ನಿಮ್ಮ ಟೇಬಲ್ ಖಚಿತವಾಗಿದೆ.",
    "I have sent the confirmation and QR code to your WhatsApp.":
        "ದೃಢೀಕರಣ ಮತ್ತು QR ಕೋಡ್ ಅನ್ನು ನಿಮ್ಮ WhatsApp ಗೆ ಕಳುಹಿಸಿದ್ದೇನೆ.",
    "I could not send the WhatsApp confirmation right now, but your booking is saved.":
        "ಈಗ WhatsApp ಗೆ ದೃಢೀಕರಣ ಕಳುಹಿಸಲು ಆಗಲಿಲ್ಲ, ಆದರೆ ನಿಮ್ಮ ಬುಕಿಂಗ್ ಉಳಿಸಲಾಗಿದೆ.",
    "Just give your name at the entrance.": "ಬಂದಾಗ ಪ್ರವೇಶದಲ್ಲಿ ನಿಮ್ಮ ಹೆಸರು ಹೇಳಿ ಸಾಕು.",
    "That time has already passed.": "ಆ ಸಮಯ ಈಗಾಗಲೇ ಕಳೆದಿದೆ.",
    "Could you choose a later time?": "ದಯವಿಟ್ಟು ನಂತರದ ಯಾವುದಾದರೂ ಸಮಯ ಆಯ್ಕೆ ಮಾಡಿ.",
    "We are closed on {date}.": "{date} ರಂದು ನಾವು ಮುಚ್ಚಿರುತ್ತೇವೆ.",
    "Could you choose another day?": "ದಯವಿಟ್ಟು ಬೇರೆ ದಿನ ಆಯ್ಕೆ ಮಾಡಿ.",
    "On {date} we are open {hours}, and the last seating is at {time}.":
        "{date} ರಂದು ನಾವು {hours} ತೆರೆದಿರುತ್ತೇವೆ, ಕೊನೆಯ ಬುಕಿಂಗ್ {time}.",
    "What time works for you?": "ನಿಮಗೆ ಯಾವ ಸಮಯ ಸರಿ ಹೋಗುತ್ತದೆ?",
    "On {date} we are open {hours}.": "{date} ರಂದು ನಾವು {hours} ತೆರೆದಿರುತ್ತೇವೆ.",
    "The last seating is at {time}.": "ಕೊನೆಯ ಬುಕಿಂಗ್ {time}.",
    "I need a valid date and time to check.": "ನೋಡಲು ಸರಿಯಾದ ದಿನಾಂಕ ಮತ್ತು ಸಮಯ ಬೇಕು.",
    "Sorry, we are fully booked at {time}.": "ಕ್ಷಮಿಸಿ, {time} ಎಲ್ಲ ಟೇಬಲ್‌ಗಳು ಬುಕ್ ಆಗಿವೆ.",
    "Yes, we have a table on {date} at {time}.": "ಹೌದು, {date}, {time} ಟೇಬಲ್ ಖಾಲಿ ಇದೆ.",
    "Would you like me to book it?": "ನಾನು ಅದನ್ನು ಬುಕ್ ಮಾಡಲೇ?",
    "That booking hold expired.": "ಆ ಬುಕಿಂಗ್ ಹೋಲ್ಡ್ ಅವಧಿ ಮುಗಿದಿದೆ.",
    "Let us check again.": "ಮತ್ತೊಮ್ಮೆ ನೋಡೋಣ.",
    "Sorry, {time} was just taken.": "ಕ್ಷಮಿಸಿ, {time} ಈಗಷ್ಟೇ ಬುಕ್ ಆಯಿತು.",
    "I could not find any upcoming booking for that number.": "ಆ ನಂಬರ್‌ಗೆ ಮುಂಬರುವ ಯಾವುದೇ ಬುಕಿಂಗ್ ಸಿಗಲಿಲ್ಲ.",
    "I could not find an active booking matching those details.": "ಆ ವಿವರಗಳಿಗೆ ಹೊಂದುವ ಯಾವುದೇ ಬುಕಿಂಗ್ ಸಿಗಲಿಲ್ಲ.",
    "Too many verification codes were requested for this number.": "ಈ ನಂಬರ್‌ಗೆ ತುಂಬಾ ಹೆಚ್ಚು ಕೋಡ್‌ಗಳನ್ನು ಕೇಳಲಾಗಿದೆ.",
    "Please try again in an hour or call us at {phone}.":
        "ದಯವಿಟ್ಟು ಒಂದು ಗಂಟೆ ನಂತರ ಪ್ರಯತ್ನಿಸಿ ಅಥವಾ {phone} ಗೆ ಕರೆ ಮಾಡಿ.",
    "To keep your booking safe, I have sent a 6-digit code to your WhatsApp.":
        "ನಿಮ್ಮ ಬುಕಿಂಗ್ ಸುರಕ್ಷಿತವಾಗಿರಲು ನಿಮ್ಮ WhatsApp ಗೆ 6 ಅಂಕಿಯ ಕೋಡ್ ಕಳುಹಿಸಿದ್ದೇನೆ.",
    "Please tell me the code to confirm the cancellation.": "ರದ್ದತಿಯನ್ನು ಖಚಿತಪಡಿಸಲು ದಯವಿಟ್ಟು ಆ ಕೋಡ್ ಹೇಳಿ.",
    "Demo mode: your verification code is {code}.": "ಡೆಮೋ ಮೋಡ್: ನಿಮ್ಮ ಕೋಡ್ {code}.",
    "Please say it to confirm the cancellation.": "ರದ್ದತಿ ಖಚಿತಪಡಿಸಲು ಅದನ್ನು ಹೇಳಿ.",
    "I cannot verify your number right now, so I cannot cancel by voice.":
        "ಈಗ ನಿಮ್ಮ ನಂಬರ್ ಪರಿಶೀಲಿಸಲು ಆಗುತ್ತಿಲ್ಲ, ಹಾಗಾಗಿ ಫೋನ್‌ನಲ್ಲಿ ರದ್ದು ಮಾಡಲು ಆಗುವುದಿಲ್ಲ.",
    "That code has expired.": "ಆ ಕೋಡ್‌ನ ಅವಧಿ ಮುಗಿದಿದೆ.",
    "Say cancel to start again.": "ಮತ್ತೆ ಶುರು ಮಾಡಲು ಕ್ಯಾನ್ಸಲ್ ಎಂದು ಹೇಳಿ.",
    "Your booking has been cancelled successfully.": "ನಿಮ್ಮ ಬುಕಿಂಗ್ ರದ್ದಾಗಿದೆ.",
    "That booking is no longer active.": "ಆ ಬುಕಿಂಗ್ ಈಗ ಸಕ್ರಿಯವಾಗಿಲ್ಲ.",
    "That code does not match.": "ಆ ಕೋಡ್ ಹೊಂದುತ್ತಿಲ್ಲ.",
    "Please try again.": "ದಯವಿಟ್ಟು ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ.",
    "Too many wrong codes.": "ತುಂಬಾ ತಪ್ಪು ಕೋಡ್‌ಗಳು.",
    "For your security I have stopped this cancellation.": "ನಿಮ್ಮ ಸುರಕ್ಷತೆಗಾಗಿ ಈ ರದ್ದತಿಯನ್ನು ನಿಲ್ಲಿಸಿದ್ದೇನೆ.",
    "I have passed this to our team with a note.": "ಇದನ್ನು ಒಂದು ಟಿಪ್ಪಣಿಯೊಂದಿಗೆ ನಮ್ಮ ತಂಡಕ್ಕೆ ತಿಳಿಸಿದ್ದೇನೆ.",
    "For anything urgent, please call us at {phone}.": "ತುರ್ತು ವಿಷಯಕ್ಕೆ ದಯವಿಟ್ಟು {phone} ಗೆ ಕರೆ ಮಾಡಿ.",
    "Please tell me the 6-digit code from your WhatsApp.": "ದಯವಿಟ್ಟು ನಿಮ್ಮ WhatsApp ಗೆ ಬಂದ 6 ಅಂಕಿಯ ಕೋಡ್ ಹೇಳಿ.",
    "Shall I go ahead with the booking?": "ಬುಕಿಂಗ್ ಮುಂದುವರಿಸಲೇ?",
    "Please tell me the 6-digit code when you are ready.": "ಸಿದ್ಧರಾದಾಗ 6 ಅಂಕಿಯ ಕೋಡ್ ಹೇಳಿ.",
    "No problem.": "ಪರವಾಗಿಲ್ಲ.",
    "What would you like to change?": "ಏನು ಬದಲಾಯಿಸಬೇಕು?",
    "Sorry, I did not catch that.": "ಕ್ಷಮಿಸಿ, ನನಗೆ ಅರ್ಥವಾಗಲಿಲ್ಲ.",
    "Shall I book it, or would you like to change something?": "ಬುಕ್ ಮಾಡಲೇ, ಅಥವಾ ಏನಾದರೂ ಬದಲಾಯಿಸಬೇಕೇ?",
    "Could you please repeat?": "ದಯವಿಟ್ಟು ಮತ್ತೊಮ್ಮೆ ಹೇಳುತ್ತೀರಾ?",
    "Okay.": "ಸರಿ.",
    "Okay, I have stopped this booking.": "ಸರಿ, ಈ ಬುಕಿಂಗ್ ನಿಲ್ಲಿಸಿದ್ದೇನೆ.",
    "How else can I help?": "ಇನ್ನೇನು ಸಹಾಯ ಮಾಡಲಿ?",
    "Thank you for calling {restaurant}.": "{restaurant} ಗೆ ಕರೆ ಮಾಡಿದ್ದಕ್ಕೆ ಧನ್ಯವಾದಗಳು.",
    "Have a wonderful day!": "ನಿಮ್ಮ ದಿನ ಶುಭವಾಗಿರಲಿ!",
    "Goodbye!": "ನಮಸ್ಕಾರ!",
    "Okay, I have cleared the current request.": "ಸರಿ, ಹಿಂದಿನ ವಿನಂತಿಯನ್ನು ತೆರವುಗೊಳಿಸಿದ್ದೇನೆ.",
    "How can I help now?": "ಈಗ ಹೇಗೆ ಸಹಾಯ ಮಾಡಲಿ?",
    "Hello!": "ನಮಸ್ಕಾರ!",
    "How can I help you today?": "ಇಂದು ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಲಿ?",
}

DAYS = {"Monday": "ಸೋಮವಾರ", "Tuesday": "ಮಂಗಳವಾರ", "Wednesday": "ಬುಧವಾರ", "Thursday": "ಗುರುವಾರ",
        "Friday": "ಶುಕ್ರವಾರ", "Saturday": "ಶನಿವಾರ", "Sunday": "ಭಾನುವಾರ"}
MONTHS = {"January": "ಜನವರಿ", "February": "ಫೆಬ್ರವರಿ", "March": "ಮಾರ್ಚ್", "April": "ಏಪ್ರಿಲ್", "May": "ಮೇ",
          "June": "ಜೂನ್", "July": "ಜುಲೈ", "August": "ಆಗಸ್ಟ್", "September": "ಸೆಪ್ಟೆಂಬರ್", "October": "ಅಕ್ಟೋಬರ್",
          "November": "ನವೆಂಬರ್", "December": "ಡಿಸೆಂಬರ್"}
PERIODS = {"morning": "ಬೆಳಿಗ್ಗೆ", "afternoon": "ಮಧ್ಯಾಹ್ನ", "evening": "ಸಂಜೆ", "night": "ರಾತ್ರಿ"}
HOUR_AT, HOUR = "ಗಂಟೆಗೆ", "ಗಂಟೆ"           # "at 8" / "8 o'clock"
FROM, UNTIL, AND, OR, NOW = "ಯಿಂದ", "ಯವರೆಗೆ", " ಮತ್ತು ", " ಅಥವಾ ", "ಈಗ, "

# ── Input: Kannada -> English for the rule readers ────────────────────────────
DIGITS = str.maketrans("೦೧೨೩೪೫೬೭೮೯", "0123456789")
NUMBERS = {
    "ಸೊನ್ನೆ": 0, "ಒಂದು": 1, "ಎರಡು": 2, "ಮೂರು": 3, "ನಾಲ್ಕು": 4, "ಐದು": 5, "ಆರು": 6, "ಏಳು": 7,
    "ಎಂಟು": 8, "ಒಂಬತ್ತು": 9, "ಹತ್ತು": 10, "ಹನ್ನೊಂದು": 11, "ಹನ್ನೆರಡು": 12, "ಹದಿನೈದು": 15, "ಇಪ್ಪತ್ತು": 20,
    "ಇಪ್ಪತ್ತೈದು": 25,
}
# "ಇಬ್ಬರು" = two people, "ನಾಲ್ವರು" = four people
PEOPLE_WORDS = {"ಒಬ್ಬರು": 1, "ಇಬ್ಬರು": 2, "ಮೂವರು": 3, "ನಾಲ್ವರು": 4, "ಐವರು": 5,
                # "for N people" (dative): ಇಬ್ಬರಿಗೆ = for two
                "ಒಬ್ಬರಿಗೆ": 1, "ಇಬ್ಬರಿಗೆ": 2, "ಮೂವರಿಗೆ": 3, "ನಾಲ್ವರಿಗೆ": 4, "ಐವರಿಗೆ": 5}

PHRASES = [
    (r"ನನ್ನ ಹೆಸರು\s+(.+?)(?:\s+ಅಂತ|\s*$|[.,।])", r"my name is \1 "), (r"ನನ್ನ ಹೆಸರು", "my name is"),
    (r"ನನ್ನ (?:ಮೊಬೈಲ್ )?ನಂಬರ್", "my number is"),
    (r"ನಾಡಿದ್ದು", "day after tomorrow"), (r"ನಾಳೆ", "tomorrow"), (r"ಇವತ್ತು|ಇಂದು", "today"),
    (r"ಗಂಟೆಗೆ|ಗಂಟೆ", " baje"), (r"ಸಂಜೆ", "evening"), (r"ರಾತ್ರಿ", "night"),
    (r"ಬೆಳಿಗ್ಗೆ|ಬೆಳಗ್ಗೆ", "morning"), (r"ಮಧ್ಯಾಹ್ನ", "afternoon"),
    (r"ಜನರಿಗೆ|ಜನರು|ಜನ", " people"), (r"ಗಾಗಿ|ಕ್ಕೆ", " for"),
    (r"ಹೌದು|ಸರಿ|ಆಯ್ತು|ಆಗಲಿ|ಓಕೆ", "yes"), (r"ಇಲ್ಲ|ಬೇಡ", "no"),
    (r"ಟೇಬಲ್", "table"), (r"ಬುಕಿಂಗ್", "booking"), (r"ಬುಕ್", "book"), (r"ರಿಸರ್ವ್", "reserve"),
    (r"ಕ್ಯಾನ್ಸಲ್|ರದ್ದು", "cancel"), (r"ಪಾರ್ಕಿಂಗ್", "parking"), (r"ಮೆನು", "menu"),
    (r"ಸಸ್ಯಾಹಾರಿ|ವೆಜ್", "vegetarian"), (r"ಮೊಬೈಲ್|ನಂಬರ್", "number"), (r"ಹೆಸರು", "name"),
    # Menu words are mostly borrowed food names; the small LLM misreads them ("ಪನೀರ್" -> "pepper")
    (r"ಪನೀರ್", "paneer"), (r"ಬಿರಿಯಾನಿ", "biryani"), (r"ಚಿಕನ್", "chicken"), (r"ಮಟನ್", "mutton"),
    (r"ಟಿಕ್ಕಾ", "tikka"), (r"ನಾನ್", "naan"), (r"ರೋಟಿ", "roti"), (r"ಲಸ್ಸಿ", "lassi"), (r"ದಾಲ್", "dal"),
    (r"ಮೀನು|ಫಿಶ್", "fish"), (r"ಪ್ರಾನ್ಸ್|ಸಿಗಡಿ", "prawn"), (r"ಕಾಫಿ", "coffee"), (r"ಚಹಾ|ಟೀ", "chai"),
    (r"ಡೆಸರ್ಟ್|ಸಿಹಿ ತಿಂಡಿ", "dessert"), (r"ಊಟ|ಆಹಾರ", "food"),
    (r"ಬೆಲೆ ಎಷ್ಟು|ಎಷ್ಟು ಬೆಲೆ|ಎಷ್ಟು ರೂಪಾಯಿ", "how much is the price"), (r"ಬೆಲೆ", "price"),
    (r"ಇದೆಯಾ|ಇದೆಯೇ|ಇದೆಯಾ\?", "is there"), (r"ಎಲ್ಲಿದೆ|ಎಲ್ಲಿ", "where"),
] + [(k, v) for v, k in DAYS.items()] + [(k, v) for v, k in MONTHS.items()]


def half_past_patterns():
    """'ಏಳುವರೆ' / 'ಏಳೂವರೆ' (seven and a half) -> '7:30'."""
    out = []
    for word, n in NUMBERS.items():
        if 1 <= n <= 12:
            stems = {word + "ವರೆ"}
            if word.endswith("ು"):
                stems |= {word[:-1] + "ೂವರೆ", word[:-1] + "ುವರೆ"}
            out += [(s, f"{n}:30") for s in stems]
    return out
