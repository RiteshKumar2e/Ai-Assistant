"""
core/training_corpus.py — JUDO's routing exam: 20,000+ labelled spoken commands.

Every entry is (command, label, args). The label is the tool that MUST handle the
command, optionally with the action it must pick: "open_app",
"computer_settings.volume_up", or "none" (small talk / answer from own
knowledge, no tool). `args` is what a correct call must contain — the right
receiver, message, folder, city, value — so the exam checks the whole TASK,
not just which tool was picked (see ARGS below). Commands mix English, Hinglish and Hindi (Devanagari)
because that is how the user actually talks to JUDO.

Built from templates x slot values x spoken fillers, with a fixed seed — the
same corpus comes out on every machine and every run, so core/routing_trainer
can resume by index and a score from last week is comparable to today's.
Nothing here calls a model or touches the network.
"""
from __future__ import annotations

import hashlib
import itertools
import random

TARGET_SIZE = 20_000
_SEED = 20260926

S = {
    "app": ["Spotify", "WhatsApp", "Notepad", "Calculator", "VS Code", "Word", "Excel", "PowerPoint",
            "Settings", "Paint", "Telegram", "Discord", "Zoom", "Teams", "Steam", "OBS", "Photoshop",
            "Task Manager", "Microsoft Store", "Edge", "File Explorer", "Command Prompt",
            "PowerShell", "Snipping Tool", "VLC", "Outlook", "OneNote", "Postman", "Figma", "Slack",
            "Android Studio", "PyCharm", "Blender", "Clock", "Epic Games", "Notion", "Obsidian"],
    "site": ["YouTube", "ChatGPT", "Gmail", "Instagram", "Facebook", "Amazon", "Flipkart", "GitHub",
             "LinkedIn", "Netflix", "Google Drive", "Reddit", "Wikipedia", "Stack Overflow",
             "Hotstar", "Swiggy", "Zomato", "IRCTC", "Google Maps", "Claude", "Gemini", "Canva", "Leetcode"],
    "url": ["youtube.com", "chatgpt.com", "github.com", "amazon.in", "flipkart.com", "wikipedia.org",
            "gmail.com", "linkedin.com", "reddit.com", "netflix.com", "leetcode.com", "irctc.co.in"],
    "contact": ["Rahul", "mom", "papa", "Priya", "Aman", "bhaiya", "didi", "Neha", "Rohit", "Sneha",
                "Vikas", "boss", "Anjali", "Karan", "Pooja", "chachu", "Arjun", "Simran", "Ravi", "Meera"],
    "msg": ["main late aaunga", "I reached home", "call me back", "khana kha liya?", "meeting 5 baje hai",
            "happy birthday", "kal milte hain", "I'm on my way", "notes bhej do", "thank you",
            "paise transfer kar diye", "10 minute me aata hoon", "running late", "good night"],
    "city": ["Delhi", "Mumbai", "Bangalore", "Patna", "Kolkata", "Chennai", "Hyderabad", "Pune", "Jaipur",
             "Lucknow", "Goa", "Ranchi", "Noida", "Chandigarh", "Indore", "Bhopal", "Varanasi", "Shimla",
             "London", "Dubai", "New York", "Singapore", "Ahmedabad", "Guwahati"],
    "song": ["Arijit Singh songs", "lofi music", "Kesariya", "rain sounds", "Believer", "Tum Hi Ho",
             "study music", "Shape of You", "bhajan", "Honey Singh", "old Kishore Kumar songs",
             "workout music", "piano music", "Sidhu Moose Wala", "AP Dhillon", "meditation music",
             "Hanuman Chalisa", "Diljit Dosanjh", "Coldplay", "punjabi songs", "90s hits"],
    "vtopic": ["how to make pasta", "Python tutorial", "iPhone 17 review", "cricket highlights",
               "React crash course", "stock market basics", "funny cat videos", "Mr Beast latest video",
               "DSA in Java", "machine learning course", "car review", "tech news"],
    "topic": ["AI", "cricket", "stock market", "ISRO", "elections", "Bitcoin", "space", "iPhone",
              "Tesla", "climate change", "IPL", "Bollywood", "startups", "OpenAI", "the budget",
              "electric cars", "SpaceX", "football", "Nvidia", "Chandrayaan"],
    "product": ["iPhone 17", "PS5", "MacBook Air", "Samsung S26", "OnePlus 14", "RTX 5090", "AirPods",
                "Royal Enfield Classic", "Tata Nexon EV", "Pixel 10", "iPad", "Apple Watch", "Kindle"],
    "question": ["who won yesterday's match", "what is the price of gold today", "who is the CEO of OpenAI",
                 "when is the next IPL match", "what happened in the news today", "Sensex kitna hai aaj",
                 "petrol ka rate kya hai", "latest iPhone kab launch hoga", "India vs Pakistan score",
                 "who is the prime minister of Japan", "dollar ka rate kya hai", "new movies this week"],
    "folder": ["Projects", "Downloads", "Documents", "Desktop", "Pictures", "Music", "Videos", "College",
               "Work", "Notes", "Assignments", "Photos", "Backup", "Screenshots"],
    "newfolder": ["test", "Projects", "my app", "college notes", "photos 2026", "backup", "demo",
                  "AI assistant", "invoices", "practice", "portfolio", "new project", "hackathon"],
    "file": ["notes.txt", "resume.pdf", "report.docx", "data.csv", "todo.txt", "main.py",
             "budget.xlsx", "photo.jpg", "invoice.pdf", "old_logs.txt", "draft.docx", "app.js"],
    "codefile": ["main.py", "app.js", "script.py", "test.py", "server.js", "game.py", "bot.py", "index.js"],
    "ext": ["pdf", "jpg", "mp4", "docx", "txt", "py", "zip", "png", "exe", "mp3"],
    "game": ["GTA V", "Valorant", "Counter-Strike 2", "Fortnite", "Cyberpunk", "Rocket League",
             "Elden Ring", "Minecraft", "Apex Legends", "Dota 2", "PUBG"],
    "time": ["5 baje", "at 6 pm", "10 minute baad", "in 30 minutes", "kal subah 7 baje", "tomorrow at 9",
             "shaam 4 baje", "raat 11 baje", "in an hour", "2 ghante baad", "at 8:30", "dopahar 2 baje"],
    "task": ["call mom", "drink water", "take medicine", "meeting join karna", "gym jaana", "submit assignment",
             "pay the electricity bill", "study for exam", "order groceries", "check email", "walk the dog",
             "doodh lana", "recharge karna"],
    "event": ["team meeting", "doctor appointment", "dentist", "project review", "birthday party",
              "interview", "college lecture", "client call", "exam", "family dinner"],
    "day": ["tomorrow", "kal", "friday", "monday", "next week", "parso", "on 5th October", "today", "aaj"],
    "script": ["calculator", "to-do list", "password generator", "web scraper", "file renamer",
               "prime number checker", "weather fetcher", "PDF merger",
               "number guessing game", "BMI calculator", "quiz program", "CSV to Excel converter"],
    "lang": ["Python", "JavaScript", "Java", "C++", "Python", "Python"],
    "project": ["todo app", "e-commerce website", "chat app", "portfolio website", "REST API",
                "blog website", "expense tracker", "Discord bot", "weather dashboard", "quiz app",
                "Flask backend", "React frontend", "attendance system", "library management system"],
    "repo": ["my AI Assistant project", "the Judo project", "my website repo", "the backend project",
             "this project", "my college project", "the portfolio repo"],
    "quiz": ["world capitals", "Indian history", "Python basics", "science", "general knowledge",
             "maths", "cricket", "space", "geography", "computer networks"],
    "unit": [("10", "km", "miles"), ("98.6", "fahrenheit", "celsius"), ("5", "kg", "pounds"),
             ("100", "dollars", "rupees"), ("50", "euro", "INR"), ("6", "feet", "cm"), ("30", "celsius", "fahrenheit"),
             ("2", "miles", "km"), ("1000", "rupees", "dollars"), ("12", "inches", "cm"), ("70", "kg", "lbs")],
    "email": ["rahul@gmail.com", "hr@company.com", "boss@office.com", "priya.sharma@gmail.com", "support@amazon.in"],
    "esubj": ["the meeting", "leave tomorrow", "project update", "the invoice", "my resume", "the report"],
    "fact": [("name", "mera naam Ritesh hai"), ("city", "main Patna me rehta hoon"), ("food", "mujhe biryani pasand hai"),
             ("job", "I work as a software developer"), ("sister", "meri sister ka naam Neha hai"),
             ("bday", "my birthday is on 12 March"), ("color", "my favourite colour is blue"),
             ("college", "main IIT me padhta hoon"), ("game", "I love playing Valorant")],
    "chat": ["kaise ho", "how are you", "tell me a joke", "ek joke sunao", "thank you", "shukriya", "good morning",
             "tumhara naam kya hai", "what can you do", "who made you", "you are awesome", "bahut badhiya",
             "explain recursion simply", "what is photosynthesis", "2 plus 2 kitna hota hai",
             "meaning of serendipity", "write a short poem on rain", "motivate me", "kya haal hai",
             "what is the capital of France", "machine learning kya hota hai", "ek shayari sunao", "hello",
             "I'm bored", "tum kya kar sakte ho", "difference between list and tuple"],
}

_PRE = ["", "", "", "", "Judo ", "Judo, ", "please ", "zara ", "yaar ", "jaldi se ", "hey Judo ", "bhai "]
_SUF = ["", "", "", "", " please", " na", " jaldi", " yaar", " abhi", " for me", " zara"]
_NO_SUFFIX = {"save_memory", "recall_memory", "none", "shutdown_judo"}   # statements, not commands

# label -> templates. {slot} fills from S; a label may carry ".action".
T: dict[str, list[str]] = {
    "open_app": ["open {app}", "{app} kholo", "{app} open karo", "launch {app}", "start {app}",
                 "{app} chalu karo", "{app} khol do", "{app} खोलो", "can you open {app}", "{app} start kar do"],
    "browser_control.go_to": ["open {site}", "{site} kholo", "{site} open karo browser me", "go to {url}",
                              "{url} pe jao", "{site} ki website kholo", "{site} खोलो", "take me to {site}",
                              "{url} open karo", "browser me {site} khol do"],
    "browser_control.search": ["google pe search karo {topic}", "search {topic} on google", "browser me {topic} search karo",
                               "google me dhundo {product}", "open google and search {vtopic}", "{topic} google karo"],
    "web_search": ["{question}", "search karke batao {question}", "tell me {question}", "pata karo {question}",
                   "{topic} ke baare me latest kya hai", "find out {question}"],
    "web_search.news": ["{topic} ki news sunao", "latest news on {topic}", "aaj ki {topic} news", "{topic} news batao",
                        "what's the latest {topic} news", "आज की {topic} खबर"],
    "web_search.price": ["{product} ki price kya hai", "how much does {product} cost", "{product} kitne ka hai",
                         "price of {product} in India", "{product} का दाम बताओ"],
    "web_search.compare": ["{product} vs {product2} compare karo", "compare {product} and {product2}",
                           "{product} ya {product2} kaunsa better hai"],
    "youtube_video.play": ["play {song}", "{song} chalao", "{song} bajao", "youtube pe {song} lagao",
                           "{vtopic} video chalao", "play {vtopic} on youtube", "{song} सुनाओ", "{song} play karo"],
    "youtube_video.summarize": ["summarize this youtube video", "is video ka summary do", "ye YouTube video summarize karo",
                                "{vtopic} video ka summary batao"],
    "youtube_video.trending": ["youtube pe trending kya hai", "show trending videos", "trending videos dikhao"],
    "media_control.play_pause": ["pause the music", "gaana pause karo", "music pause karo", "resume the song",
                                 "gaana fir se chalao", "pause karo", "गाना पॉज़ करो", "resume karo music"],
    "media_control.next": ["next song", "agla gaana", "skip this song", "ye gaana skip karo", "next track please", "अगला गाना"],
    "media_control.previous": ["previous song", "pichla gaana lagao", "go back a track", "last song fir se"],
    "computer_settings.volume_up": ["volume badhao", "increase the volume", "awaaz tez karo", "volume up",
                                    "sound badha do", "आवाज़ बढ़ाओ", "thoda loud karo"],
    "computer_settings.volume_down": ["volume kam karo", "decrease volume", "awaaz dheemi karo", "volume down", "आवाज़ कम करो"],
    "computer_settings.volume_set": ["volume {n} pe set karo", "set volume to {n}", "volume {n} percent kar do"],
    "computer_settings.mute": ["mute karo", "mute the sound", "awaaz band karo", "sound off kar do"],
    "computer_settings.brightness_up": ["brightness badhao", "increase brightness", "screen bright karo", "brightness up"],
    "computer_settings.brightness_down": ["brightness kam karo", "screen dim karo", "decrease brightness", "brightness low kar do"],
    "computer_settings.close_app": ["close {app}", "{app} band karo", "{app} band kar do", "{app} बंद करो", "quit {app}"],
    "computer_settings.full_screen": ["fullscreen karo", "full screen kar do", "make it fullscreen", "ise bada karo full screen"],
    "computer_settings.minimize": ["minimize this window", "window chhota karo", "minimize karo"],
    "computer_settings.maximize": ["maximize the window", "window maximize karo"],
    "computer_settings.lock_screen": ["lock the screen", "screen lock karo", "PC lock kar do", "lock my computer"],
    "computer_settings.screenshot": ["take a screenshot", "screenshot le lo", "ek screenshot lo", "स्क्रीनशॉट लो"],
    "computer_settings.dark_mode": ["dark mode on karo", "turn on dark mode", "dark theme laga do", "switch to dark mode"],
    "computer_settings.toggle_wifi": ["wifi off karo", "turn off wifi", "wifi band kar do", "wifi on karo"],
    "computer_settings.shutdown": ["shutdown the computer", "PC band kar do", "computer shut down karo", "laptop band karo"],
    "computer_settings.restart": ["restart the computer", "PC restart karo", "laptop restart kar do"],
    "computer_settings.scroll_down": ["scroll down", "neeche scroll karo", "thoda neeche jao"],
    "computer_settings.scroll_up": ["scroll up", "upar scroll karo", "thoda upar jao"],
    "computer_settings.new_tab": ["new tab kholo", "open a new tab", "naya tab khol do"],
    "computer_settings.close_tab": ["close this tab", "ye tab band karo", "tab close kar do"],
    "computer_settings.save": ["save karo", "save this file", "ctrl s dabao", "file save kar do"],
    "computer_settings.show_desktop": ["show desktop", "minimize everything and show the desktop", "sab minimize karke desktop dikhao"],
    "computer_settings.task_manager": ["open task manager", "task manager kholo"],
    "computer_settings.switch_window": ["switch window", "dusri window pe jao", "alt tab karo"],
    "computer_settings.refresh_page": ["refresh the page", "page reload karo", "refresh karo"],
    "computer_settings.zoom_in": ["zoom in", "zoom karo", "text bada karo"],
    "computer_settings.copy": ["copy karo", "copy this", "ye copy kar do"],
    "computer_settings.paste": ["paste karo", "paste it here", "yahan paste kar do"],
    "computer_control": ["click on the {btn} button", "{btn} button pe click karo", "right click karo yahan",
                         "double click on the {btn} icon", "type '{msg}' in this box", "is box me likho {msg}",
                         "mouse ko top right le jao", "screen pe {btn} dhundh ke click karo",
                         "{app} window pe focus karo", "is field me mera email type karo"],
    "screen_process": ["what's on my screen", "screen pe kya hai", "meri screen dekho", "look at my screen and tell me the error",
                       "ye screen pe kya likha hai", "camera se dekho", "look at me through the camera",
                       "what am I holding", "screen dekh ke batao kya galat hai", "स्क्रीन पर क्या है"],
    "close_camera": ["close the camera", "camera band karo", "stop the camera", "camera off kar do"],
    "system_status": ["CPU usage kitna hai", "how much RAM is used", "system ka status batao", "PC garam ho raha hai kya",
                      "CPU temperature kya hai", "check system performance", "laptop slow kyu hai check karo", "GPU usage batao"],
    "manage_monitor.add": ["monitor {topic} news for me", "{topic} ko track karo", "keep an eye on {topic}",
                           "{topic} follow karo aur update dena"],
    "manage_monitor.list": ["what topics are you monitoring", "kya kya track kar rahe ho"],
    "manage_monitor.remove": ["stop monitoring {topic}", "{topic} track karna band karo"],
    "shutdown_judo": ["bye Judo", "goodbye", "Judo band ho jao", "chalo bye", "exit Judo", "that's all, shut down Judo",
                      "tum band ho jao", "alvida"],
    "save_memory": ["remember that {fact}", "yaad rakhna {fact}", "{fact}", "note kar lo {fact}"],
    "recall_memory": ["what do you remember about me", "mere baare me kya yaad hai", "meri sister ka naam kya hai",
                      "what's my favourite food", "tumhe mera birthday yaad hai", "maine kaunsa college bataya tha"],
    "undo": ["undo", "undo that", "wapas karo", "galat kiya, wapas karo", "revert it", "put it back",
             "nahi ye nahi, undo karo", "jo abhi kiya wo cancel karo", "पहले जैसा करो"],
    "agent_task": ["open {repo} and check why login fails", "{repo} me bug dhundo aur fix karo", "run the tests in {repo}",
                   "commit the changes and push", "git push kar do", "open a PR for this", "github issues check karo",
                   "fix it", "test karke batao sab pass ho raha hai kya", "deploy karo {repo}",
                   "claude code ke liye prompt bana do {repo} ke liye",
                   "what is the agent doing", "agent ko roko", "find the bug, fix it, test it and push"],
    "code_helper": ["write a {lang} {script}", "ek {script} ka {lang} code likho", "{lang} me {script} banao",
                    "make a {lang} script for {script}", "run {codefile}", "{codefile} ko run karo", "explain this code",
                    "{codefile} me error fix karo", "is code ko samjhao", "{script} ka program bana do"],
    "dev_agent": ["build a {project}", "ek {project} bana do", "{project} project banao {lang} me",
                  "create a complete {project} from scratch", "mujhe ek {project} chahiye poora", "पूरा {project} बनाओ"],
    "file_controller.create_folder": ["make a folder {newfolder}", "{newfolder} naam ka folder banao", "create folder {newfolder}",
                                      "ek folder bana do {newfolder}", "desktop pe {newfolder} folder banao", "{newfolder} फोल्डर बनाओ"],
    "file_controller.create_file": ["create a file {file}", "{file} naam ki file banao", "ek nayi file bana do {file}"],
    "file_controller.delete": ["delete {file}", "{file} delete kar do", "{file} hata do", "remove {file} from desktop"],
    "file_controller.rename": ["rename {file} to final_{file}", "{file} ka naam badlo", "{file} rename karo"],
    "file_controller.move": ["move {file} to {folder}", "{file} ko {folder} me daal do", "{file} {folder} me shift karo"],
    "file_controller.copy": ["copy {file} to {folder}", "{file} ki copy {folder} me bana do"],
    "file_controller.find": ["find {file}", "{file} kahan hai dhundo", "search for all {ext} files", "saari {ext} files dhundo"],
    "file_controller.list": ["{folder} me kya kya hai", "list files in {folder}", "{folder} folder ki files batao"],
    "file_controller.largest": ["sabse badi files kaunsi hain", "show the largest files", "find big files eating space"],
    "file_controller.disk_usage": ["disk space kitna bacha hai", "how much storage is left", "C drive kitni bhari hai"],
    "file_controller.read": ["read {file}", "{file} me kya likha hai padho", "open {file} and read it to me"],
    "open_folder": ["open {folder} folder", "{folder} folder kholo", "{folder} folder dikhao", "show me the {folder} folder",
                    "{folder} फोल्डर खोलो", "open {folder} in VS Code", "{folder} ko VS Code me kholo",
                    "right click karke {folder} ko code se kholo", "{folder} me claude code kholo"],
    "desktop_control.wallpaper": ["wallpaper change karo", "change my wallpaper", "naya wallpaper laga do", "set a nature wallpaper"],
    "desktop_control.organize": ["organize my desktop", "desktop saaf karo", "desktop ki files arrange karo", "desktop clean kar do"],
    "file_processor": ["summarize the file I uploaded", "is PDF ka summary do", "ye uploaded image me kya hai",
                       "convert this uploaded file to Word", "is CSV ko analyze karo", "upload ki hui file translate karo",
                       "is photo ko compress karo", "extract text from this uploaded PDF"],
    "flight_finder": ["flights from {city} to {city2} {day}", "{city} se {city2} ki flight dhundo", "cheapest flight to {city2} {day}",
                      "{city} to {city2} flight kitne ki hai", "{city} से {city2} फ्लाइट"],
    "game_updater.update": ["update {game}", "{game} update karo", "steam games update kar do", "saare games update karo"],
    "game_updater.install": ["install {game}", "{game} download karo steam se", "{game} install kar do"],
    "game_updater.list": ["mere installed games dikhao", "which games are installed", "list my steam games"],
    "reminder": ["remind me {time} to {task}", "{time} yaad dilana {task}", "{time} ka reminder laga do {task}",
                 "set a reminder {time} for {task}", "{time} alarm laga do", "{time} mujhe bata dena {task}"],
    "calendar_agenda.add": ["add {event} {day} to my calendar", "calendar me {event} daal do {day}",
                            "{day} {event} schedule karo calendar me", "{event} {day} ko agenda me add karo"],
    "calendar_agenda.list": ["what's on my calendar {day}", "{day} ka schedule kya hai", "mera agenda batao", "what do I have this week"],
    "send_message": ["{contact} ko message karo {msg}", "WhatsApp {contact} that {msg}", "{contact} ko bol do {msg}",
                     "send a message to {contact}: {msg}", "{contact} ko WhatsApp pe likho {msg}",
                     "telegram pe {contact} ko bhejo {msg}", "{contact} को मैसेज करो {msg}"],
    "send_email": ["email {email} about {esubj}", "{email} ko mail bhejo {esubj} ke baare me",
                   "send an email to {email} saying {msg}", "{email} ko ek email likho {esubj} pe"],
    "weather_report": ["weather in {city}", "{city} ka mausam kaisa hai", "aaj mausam kaisa hai", "will it rain in {city} tomorrow",
                       "{city} me kitni garmi hai", "बारिश होगी क्या {city} में", "what's the temperature outside"],
    "unit_converter": ["convert {uv} {uf} to {ut}", "{uv} {uf} kitne {ut} hote hain", "{uv} {uf} in {ut}", "{uv} {uf} ko {ut} me badlo"],
    "quiz_mode.start": ["quiz me on {quiz}", "{quiz} pe quiz lo", "mera {quiz} ka test lo", "{quiz} ke questions pucho"],
    "none": ["{chat}"],
}

# Two tools that genuinely do the same job — either pick is a right answer.
EQUIVALENT = {
    "computer_settings.copy": {"computer_control.copy"},
    "computer_settings.paste": {"computer_control.paste"},
    "computer_settings.screenshot": {"computer_control.screenshot"},
    "computer_settings.close_tab": {"browser_control.close_tab"},
    "computer_settings.new_tab": {"browser_control.new_tab"},
    "computer_settings.save": {"computer_control.hotkey"},
    "computer_settings.scroll_down": {"computer_control.scroll", "browser_control.scroll"},
    "computer_settings.scroll_up": {"computer_control.scroll", "browser_control.scroll"},
    "desktop_control.organize": {"file_controller.organize_desktop", "desktop_control.clean"},
    "file_controller.list": {"desktop_control.list"},
    "desktop_control.wallpaper": {"desktop_control.task"},
    "file_controller.read": {"file_processor"},   # a bare tool name = any of its actions
    "computer_settings.task_manager": {"open_app"},              # open_app("Task Manager") opens it too
    "computer_settings.refresh_page": {"browser_control.reload"},
    "computer_settings.switch_window": {"computer_control.hotkey"},
    "computer_settings.zoom_in": {"computer_control.hotkey"},
}

# What a correct call must CONTAIN, per label: {param: expected}. A param may
# list alternatives ("path|name" — the tool accepts either); "?" after it means
# leaving it out is also right (the tool's default is that value). Expected
# values: "{slot}" or "{a|b}" (first slot the template used; skipped if none),
# or a literal. Prefix "~" = loose (half the words must appear, the model may
# rephrase), "#" = number, "@" = unit; otherwise every word must appear.
ARGS: dict[str, dict[str, str]] = {
    "open_app": {"app_name": "{app}"},
    "browser_control.go_to": {"url": "{site|url}"},
    "browser_control.search": {"query": "~{topic|product|vtopic}"},
    "web_search": {"query": "{topic}"},
    "web_search.news": {"query": "{topic}"},
    "web_search.price": {"query": "{product}"},
    "web_search.compare": {"items|query": "{product}"},
    "youtube_video.play": {"query": "~{song|vtopic}"},
    "computer_settings.volume_set": {"value": "#{n}"},
    "computer_settings.close_app": {"value|description": "{app}"},
    "computer_control": {"text|title|description": "~{btn|msg|app}"},
    "manage_monitor.add": {"topic": "{topic}"},
    "manage_monitor.remove": {"topic": "{topic}"},
    "code_helper": {"file_path|description": "{codefile}", "language?": "{lang}"},
    "dev_agent": {"description": "~{project}"},
    "file_controller.create_folder": {"path|name": "{newfolder}"},
    "file_controller.create_file": {"path|name": "{file}"},
    "file_controller.delete": {"path|name": "{file}"},
    "file_controller.rename": {"path|name": "{file}"},
    "file_controller.move": {"path|name": "{file}", "destination": "{folder}"},
    "file_controller.copy": {"path|name": "{file}", "destination": "{folder}"},
    "file_controller.find": {"name|extension": "{file|ext}"},
    "file_controller.list": {"path": "{folder}"},
    "file_controller.read": {"path|name": "{file}"},
    "open_folder": {"folder_path": "{folder}"},
    "flight_finder": {"origin": "{city}", "destination": "{city2}"},
    "game_updater.update": {"game_name": "{game}"},
    "game_updater.install": {"game_name": "{game}"},
    "reminder": {"message": "~{task}"},
    "calendar_agenda.add": {"title": "~{event}"},
    "send_message": {"receiver": "{contact}", "message_text": "~{msg}"},
    "send_email": {"to": "{email}"},
    "weather_report": {"city": "{city}"},
    "unit_converter": {"value": "#{uv}", "from_unit": "@{uf}", "to_unit": "@{ut}"},
    "quiz_mode.start": {"topic": "{quiz}"},
}
# Extra expectations that depend on the wording of the template itself.
ARGS_IF: dict[str, list[tuple[str, dict[str, str]]]] = {
    "open_folder": [("vs code", {"open_in": "vscode"}), ("code se", {"open_in": "vscode"}),
                    ("claude", {"open_in": "claude"})],
    "screen_process": [("camera", {"angle": "camera"}), ("holding", {"angle": "camera"}),
                       ("screen", {"angle?": "screen"})],
    "send_message": [("telegram", {"platform": "telegram"}), ("whatsapp", {"platform?": "whatsapp"})],
}


def expected_args(label: str, tpl: str, fills: dict) -> dict[str, str]:
    out = {}
    for param, spec in ARGS.get(label, {}).items():
        prefix = spec[0] if spec[0] in "~#@" else ""
        slots = spec[len(prefix):].strip("{}").split("|")
        val = next((fills[k] for k in slots if k in fills), None)
        if val is not None:
            out[param] = prefix + str(val)
    for needle, extra in ARGS_IF.get(label, []):
        if needle in tpl.lower():
            out.update(extra)
            break
    return out


_EXTRA = {"btn": ["Submit", "OK", "Next", "Login", "Download", "Send", "Save", "Cancel", "Start", "Install"],
          "n": ["20", "30", "50", "60", "75", "80", "100", "10", "40"]}


def _fills(tpl: str, rng: random.Random) -> dict:
    out = {}
    for key in ("app", "site", "url", "contact", "msg", "city", "song", "vtopic", "topic", "product", "question",
                "folder", "newfolder", "file", "codefile", "ext", "game", "time", "task", "event", "day", "script", "lang",
                "project", "repo", "quiz", "email", "esubj", "chat"):
        if "{" + key + "}" in tpl:
            out[key] = rng.choice(S[key])
    for k, vals in _EXTRA.items():
        if "{" + k + "}" in tpl:
            out[k] = rng.choice(vals)
    if "{product2}" in tpl:
        out["product2"] = rng.choice([p for p in S["product"] if p != out.get("product")])
    if "{city2}" in tpl:
        out["city2"] = rng.choice([c for c in S["city"] if c != out.get("city")])
    if "{uv}" in tpl:
        out["uv"], out["uf"], out["ut"] = rng.choice(S["unit"])
    if "{fact}" in tpl:
        out["fact"] = rng.choice(S["fact"])[1]
    return out


def build(size: int = TARGET_SIZE) -> list[tuple[str, str, dict]]:
    """Deterministic, de-duplicated, label-balanced corpus of `size` tasks:
    (spoken command, label, expected args)."""
    rng = random.Random(_SEED)
    labels = list(T)
    per_label: dict[str, dict[str, dict]] = {l: {} for l in labels}
    # Upper bound on unique phrasings per label keeps small labels (close_camera)
    # from spinning forever; the leftover budget flows to the rich ones.
    n_actions = {}
    for l in labels:
        n_actions[l.split(".")[0]] = n_actions.get(l.split(".")[0], 0) + 1
    for l in labels:
        cap = int(1.3 * size / len(n_actions) / n_actions[l.split(".")[0]]) + 20
        tries = 0
        while len(per_label[l]) < cap and tries < cap * 25:
            tries += 1
            tpl = rng.choice(T[l])
            fills = _fills(tpl, rng)
            text = tpl.format(**fills)
            text = (rng.choice(_PRE) + text + ("" if l in _NO_SUFFIX else rng.choice(_SUF))).strip()
            text = text[0].upper() + text[1:] if rng.random() < .3 else text
            per_label[l].setdefault(text, expected_args(l, tpl, fills))
    # A phrasing generated under two labels is genuinely ambiguous — it has no
    # single right answer, so it cannot be an exam question.
    owners: dict[str, set] = {}
    for l, v in per_label.items():
        for c in v:
            owners.setdefault(c.lower(), set()).add(l)
    pools = {l: sorted(c for c in v if len(owners[c.lower()]) == 1) for l, v in per_label.items()}
    for v in pools.values():
        rng.shuffle(v)
    # Interleave actions within each tool, then round-robin across tools — every
    # tool gets an equal share (computer_settings has 28 actions, it must not
    # drown the rest) and any prefix of the corpus is balanced too.
    by_tool: dict[str, list] = {}
    for l, cmds in pools.items():
        by_tool.setdefault(l.split(".")[0], []).append([(c, l, per_label[l][c]) for c in cmds])
    streams = [[x for grp in itertools.zip_longest(*g) for x in grp if x] for g in by_tool.values()]
    out = [x for grp in itertools.zip_longest(*streams) for x in grp if x]
    return out[:size]


def fingerprint(corpus: list[tuple[str, str, dict]]) -> str:
    return hashlib.sha1("\n".join(f"{c}\t{l}\t{sorted(a.items())}" for c, l, a in corpus).encode()).hexdigest()[:12]


def sample(rng: random.Random) -> tuple[str, str, dict[str, str]]:
    """One fresh random task, for training data streamed at any scale (the
    Kaggle router training draws tens of millions): tool picked uniformly,
    then one of its actions, then a template and slot values. Returns
    (command, label, {param: value exactly as it appears in the command}) —
    constants implied by wording (open_in="vscode") are left out, since no
    span of the sentence spells them."""
    label = rng.choice(_TOOLS[rng.choice(_TOOL_NAMES)])
    tpl = rng.choice(T[label])
    fills = _fills(tpl, rng)
    text = tpl.format(**fills)
    text = (rng.choice(_PRE) + text + ("" if label in _NO_SUFFIX else rng.choice(_SUF))).strip()
    text = text[0].upper() + text[1:] if rng.random() < .3 else text
    implied = {k for _, extra in ARGS_IF.get(label, []) for k in extra}
    args = {}
    for spec, want in expected_args(label, tpl, fills).items():
        value = want.lstrip("~#@")
        if spec not in implied and value.lower() in text.lower():
            args[spec.rstrip("?").split("|")[0]] = value
    return text, label, args


_TOOLS: dict[str, list[str]] = {}
for _label in T:
    _TOOLS.setdefault(_label.split(".")[0], []).append(_label)
_TOOL_NAMES = sorted(_TOOLS)
