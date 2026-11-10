import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def main():
    cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short"]
    print("Running full pytest suite to pytest_and_apptest.log...")
    with open("pytest_and_apptest.log", "w", encoding="utf-8", errors="replace") as f:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in proc.stdout:
            f.write(line)
            f.flush()
        proc.wait()
    print(f"\nPytest finished with return code: {proc.returncode}")
    sys.exit(proc.returncode)

if __name__ == "__main__":
    main()
