import subprocess

def create_patch():
    # Run git diff against HEAD and write in UTF-8
    res = subprocess.run(["git", "diff", "HEAD"], capture_output=True, check=True)
    with open("git_diff_integrated_audit.patch", "wb") as f:
        f.write(res.stdout)
    print("git_diff_integrated_audit.patch created in UTF-8. Size:", len(res.stdout), "bytes")

if __name__ == "__main__":
    create_patch()
