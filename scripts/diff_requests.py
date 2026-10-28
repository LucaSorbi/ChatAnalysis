with open(r"C:\Users\Luca\.lmstudio\server-logs\2026-10\2026-10-26.1.log", "r", encoding="utf-8", errors="ignore") as f:
    text = f.read()

# Find task 4617
pos_4617 = text.find("task 4617")
req_4617 = text.rfind("Received request: POST to /v1/chat/completions", 0, pos_4617)
end_4617 = text.find("Running chat completion", req_4617)
body_4617 = text[req_4617:end_4617]

# Find task 43829
pos_43829 = text.find("task 43829")
req_43829 = text.rfind("Received request: POST to /v1/chat/completions", 0, pos_43829)
end_43829 = text.find("Running chat completion", req_43829)
body_43829 = text[req_43829:end_43829]

print("=== LENGTHS ===")
print("4617 body len:", len(body_4617))
print("43829 body len:", len(body_43829))

# Find system prompt in both
import difflib

lines_4617 = body_4617.splitlines()
lines_43829 = body_43829.splitlines()

diff = difflib.unified_diff(lines_4617, lines_43829, fromfile="morning_4617", tofile="evening_43829")
print("\n=== DIFF BETWEEN MORNING AND EVENING REQUEST BODY ===")
for line in list(diff)[:50]:
    print(line)
