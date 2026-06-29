"""Tabashir Auto-Apply GUI — pick a client, auto-find suitable scraped jobs, apply.

Flow: choose a client -> the app ranks the scraped board jobs (those with a web
apply form) against that client -> tick the ones to apply to -> Start. Each job is
applied to in turn; if a CAPTCHA appears you solve it in the browser and click
Continue (the agent never bypasses it).
"""

import subprocess
import threading
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
import psycopg2
import psycopg2.extras

DB_CONFIG = {
    "host": "77.243.85.225",
    "port": 5432,
    "database": "tabashir",
    "user": "postgres",
    "password": "tabashir2025",
}

# Scraped jobs live in the `scraper` schema; only board sources have a real web
# apply form (mourjan/telegram are email-apply and handled by the email program).
JOBS_SCHEMA = "scraper"
WEB_SOURCES = ("gulftalent", "bayt", "linkedin", "indeed")

_ROOT   = Path(__file__).resolve().parent
_PYTHON = _ROOT / ".venv" / "Scripts" / "python.exe"
_SCRIPT = _ROOT / "tabashir_auto_apply.py"

# Same dedicated bot profile the apply agent uses — "Setup Login" opens it so the
# user can sign into Bayt / LinkedIn / Google once; the session persists.
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BOT_PROFILE_DIR = _ROOT / "bot_chrome_profile"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_STOP = {"and", "or", "the", "a", "an", "in", "at", "for", "of", "to", "with",
         "-", "&", "uae", "dubai", "abu", "dhabi", "sharjah", "job", "jobs"}


# ── Data ───────────────────────────────────────────────────────────────────────

def _connect():
    return psycopg2.connect(**DB_CONFIG, cursor_factory=psycopg2.extras.RealDictCursor)


def get_clients() -> list[dict]:
    """Fresh clients with remaining quota and a CV on file."""
    try:
        conn = _connect(); cur = conn.cursor()
        cur.execute("""
            SELECT id, name, email, major, skills, keywords, location,
                   jobs_to_apply_number
            FROM clients
            WHERE client_type = 'fresh'
              AND jobs_to_apply_number > 0
              AND filename IS NOT NULL AND filename != ''
            ORDER BY name
        """)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"get_clients error: {e}")
        return []


def get_web_jobs() -> list[dict]:
    """Scraped board jobs that have a real web apply URL."""
    try:
        conn = _connect(); cur = conn.cursor()
        cur.execute(f"""
            SELECT id, source, title, company, location, url, description
            FROM {JOBS_SCHEMA}.jobs
            WHERE status = 'active'
              AND url LIKE 'http%%'
              AND source = ANY(%s)
            ORDER BY last_seen_at DESC
        """, (list(WEB_SOURCES),))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"get_web_jobs error: {e}")
        return []


def _client_terms(client: dict) -> set[str]:
    blob = f"{client.get('major') or ''} {client.get('skills') or ''} {client.get('keywords') or ''}"
    return {w for w in blob.lower().replace(",", " ").split() if len(w) > 2 and w not in _STOP}


def rank_jobs_for_client(client: dict, jobs: list[dict]) -> list[dict]:
    """Score each job against the client's major/skills/keywords; best first."""
    terms = _client_terms(client)
    scored = []
    for j in jobs:
        hay = f"{j['title'] or ''} {j['description'] or ''}".lower()
        score = sum(1 for w in terms if w in hay)
        if score > 0:
            scored.append((score, j))
    scored.sort(key=lambda t: t[0], reverse=True)
    out = []
    for score, j in scored:
        j = dict(j); j["_score"] = score
        out.append(j)
    return out


# ── GUI ─────────────────────────────────────────────────────────────────────

class AutoApplyGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Tabashir Auto-Apply")
        self.geometry("900x720")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._clients: list[dict] = []
        self._label_to_client: dict[str, dict] = {}
        self._all_jobs: list[dict] = []
        self._job_rows: list[tuple[ctk.CTkCheckBox, dict]] = []
        self._proc = None
        self._stop_flag = False

        self._build_ui()
        self._load_data()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self):
        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="ew")
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="Client", font=("Arial", 14, "bold")).grid(
            row=0, column=0, padx=15, pady=12, sticky="w")
        self._client_box = ctk.CTkComboBox(top, values=["Loading clients…"],
                                            command=self._on_client_selected)
        self._client_box.grid(row=0, column=1, padx=(0, 10), pady=12, sticky="ew")
        ctk.CTkButton(top, text="🔐 Setup Login", width=130,
                      fg_color="#37474F", hover_color="#263238",
                      command=self._setup_login).grid(row=0, column=2, padx=(0, 15), pady=12)
        self._client_info = ctk.CTkLabel(top, text="", text_color="#81C784", anchor="w")
        self._client_info.grid(row=1, column=0, columnspan=2, padx=15, pady=(0, 10), sticky="w")

        # Jobs card
        mid = ctk.CTkFrame(self)
        mid.grid(row=1, column=0, padx=15, pady=5, sticky="ew")
        mid.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(mid, fg_color="transparent")
        head.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        self._jobs_title = ctk.CTkLabel(head, text="Suitable Jobs", font=("Arial", 14, "bold"))
        self._jobs_title.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="All", width=50, command=lambda: self._check_all(True)).grid(row=0, column=1, padx=4)
        ctk.CTkButton(head, text="None", width=50, command=lambda: self._check_all(False)).grid(row=0, column=2, padx=4)

        self._jobs_frame = ctk.CTkScrollableFrame(mid, height=230)
        self._jobs_frame.grid(row=1, column=0, padx=10, pady=10, sticky="ew")
        self._jobs_frame.grid_columnconfigure(0, weight=1)

        ctrl = ctk.CTkFrame(mid, fg_color="transparent")
        ctrl.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")
        ctrl.grid_columnconfigure(2, weight=1)
        self._start_btn = ctk.CTkButton(ctrl, text="▶ Start Auto-Apply", height=38,
                                        font=("Arial", 13, "bold"),
                                        fg_color="#2E7D32", hover_color="#1B5E20",
                                        command=self._start, state="disabled")
        self._start_btn.grid(row=0, column=0, padx=(0, 6))
        self._stop_btn = ctk.CTkButton(ctrl, text="■ Stop", width=80,
                                       fg_color="#C62828", hover_color="#8E0000",
                                       command=self._stop, state="disabled")
        self._stop_btn.grid(row=0, column=1)
        self._status = ctk.CTkLabel(ctrl, text="", text_color="gray", font=("Arial", 11))
        self._status.grid(row=0, column=2, padx=10, sticky="w")

        self._captcha_btn = ctk.CTkButton(
            ctrl, text="✅ I solved the CAPTCHA — Continue",
            command=self._continue_captcha, height=34,
            fg_color="#E65100", hover_color="#BF360C", font=("Arial", 12, "bold"))
        # gridded only when a CAPTCHA appears

        # Log
        logf = ctk.CTkFrame(self)
        logf.grid(row=2, column=0, padx=15, pady=(5, 15), sticky="nsew")
        logf.grid_columnconfigure(0, weight=1)
        logf.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(logf, text="Output", font=("Arial", 12, "bold")).grid(
            row=0, column=0, padx=15, pady=(10, 4), sticky="w")
        self._log = ctk.CTkTextbox(logf, font=("Courier New", 11), wrap="word")
        self._log.grid(row=1, column=0, padx=15, pady=(0, 15), sticky="nsew")

    # ── One-time login setup (persistent bot profile) ────────────────────────
    def _setup_login(self):
        """Open the bot's Chrome profile so the user logs into the job sites once.

        Whatever they sign into here (Bayt, LinkedIn, Google) is saved in the
        profile and reused by every apply — no passwords stored, no auto-login.
        """
        if self._proc is not None:
            self._status.configure(text="Stop the current run before Setup Login.",
                                   text_color="#FF6B6B")
            return
        BOT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        if not Path(CHROME_EXE).exists():
            self._status.configure(text=f"Chrome not found at {CHROME_EXE}",
                                   text_color="#FF6B6B")
            return
        try:
            subprocess.Popen([
                CHROME_EXE,
                f"--user-data-dir={BOT_PROFILE_DIR}",
                "--no-first-run", "--no-default-browser-check",
                "https://www.bayt.com/en/login/",
            ])
            self._status.configure(
                text="Chrome opened — log into Bayt / LinkedIn, then CLOSE it. Sessions are saved.",
                text_color="#81C784")
            self._log_line("Setup Login: sign into the job sites in the Chrome window, then close it.")
        except Exception as e:
            self._status.configure(text=f"Could not open Chrome: {e}", text_color="#FF6B6B")

    # ── Data loading ─────────────────────────────────────────────────────────
    def _load_data(self):
        self._status.configure(text="Loading clients & jobs…", text_color="gray")

        def work():
            clients = get_clients()
            jobs = get_web_jobs()
            self.after(0, self._on_data_loaded, clients, jobs)

        threading.Thread(target=work, daemon=True).start()

    def _on_data_loaded(self, clients, jobs):
        self._clients = clients
        self._all_jobs = jobs
        self._label_to_client = {
            f"{c['name']}  —  {c['major'] or '—'}  (quota {c['jobs_to_apply_number']})": c
            for c in clients
        }
        labels = list(self._label_to_client.keys()) or ["No available clients"]
        self._client_box.configure(values=labels)
        self._client_box.set(labels[0])
        self._status.configure(
            text=f"{len(clients)} clients · {len(jobs)} web jobs available", text_color="gray")
        if clients:
            self._on_client_selected(labels[0])

    # ── Client → jobs ────────────────────────────────────────────────────────
    def _on_client_selected(self, label: str):
        client = self._label_to_client.get(label)
        if not client:
            return
        self._client = client
        quota = client["jobs_to_apply_number"] or 0
        self._client_info.configure(
            text=f"  {client['name']}  |  {client['major'] or '—'}  |  quota: {quota}")
        ranked = rank_jobs_for_client(client, self._all_jobs)[:40]
        self._render_jobs(ranked, preselect=quota)

    def _render_jobs(self, jobs: list[dict], preselect: int):
        for w in self._jobs_frame.winfo_children():
            w.destroy()
        self._job_rows = []
        if not jobs:
            ctk.CTkLabel(self._jobs_frame, text="No matching jobs for this client.",
                         text_color="#FF6B6B").grid(row=0, column=0, padx=10, pady=10, sticky="w")
            self._start_btn.configure(state="disabled")
            self._jobs_title.configure(text="Suitable Jobs")
            return
        for i, j in enumerate(jobs):
            var = ctk.StringVar(value="on" if i < preselect else "off")
            txt = f"[{j['source']}] {(j['title'] or '')[:70]}   ·   match {j['_score']}"
            cb = ctk.CTkCheckBox(self._jobs_frame, text=txt, variable=var,
                                 onvalue="on", offvalue="off")
            cb.grid(row=i, column=0, padx=8, pady=3, sticky="w")
            self._job_rows.append((cb, j))
        self._jobs_title.configure(text=f"Suitable Jobs ({len(jobs)} found, top {preselect} ticked)")
        self._start_btn.configure(state="normal")

    def _check_all(self, on: bool):
        for cb, _ in self._job_rows:
            cb.select() if on else cb.deselect()

    def _selected_jobs(self) -> list[dict]:
        return [j for cb, j in self._job_rows if cb.get() == "on"]

    # ── Run batch ────────────────────────────────────────────────────────────
    def _start(self):
        jobs = self._selected_jobs()
        if not jobs:
            self._status.configure(text="Tick at least one job.", text_color="#FF6B6B")
            return
        self._stop_flag = False
        self._start_btn.configure(state="disabled", text="Running…")
        self._stop_btn.configure(state="normal")
        self._client_box.configure(state="disabled")
        self._log.delete("1.0", "end")
        self._hide_captcha_btn()
        threading.Thread(target=self._run_batch, args=(self._client, jobs), daemon=True).start()

    def _stop(self):
        self._stop_flag = True
        self._status.configure(text="Stopping after current job…", text_color="#FFA500")
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _run_batch(self, client, jobs):
        total = len(jobs)
        applied = 0
        for idx, job in enumerate(jobs, 1):
            if self._stop_flag:
                break
            self.after(0, self._status.configure,
                       {"text": f"Applying {idx}/{total}…", "text_color": "#FFA500"})
            self.after(0, self._log_line, "\n" + "=" * 60)
            self.after(0, self._log_line, f"[{idx}/{total}] {job['title'][:60]}")
            self.after(0, self._log_line, f"    {job['url']}")
            ok = self._apply_one(client["id"], job)
            applied += 1 if ok else 0
        self.after(0, self._finish_batch, applied, total)

    def _apply_one(self, client_id, job) -> bool:
        cmd = [str(_PYTHON), "-u", str(_SCRIPT), str(client_id), job["url"], job["title"] or ""]
        if job.get("location"):
            cmd.append(job["location"])
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1)
            self._proc = proc
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self.after(0, self._log_line, line)
                if "CAPTCHA DETECTED" in line:
                    self.after(0, self._captcha_prompt)
            proc.wait()
            return proc.returncode == 0
        except Exception as e:
            self.after(0, self._log_line, f"Error: {e}")
            return False
        finally:
            self._proc = None
            self.after(0, self._hide_captcha_btn)

    def _finish_batch(self, applied, total):
        self._start_btn.configure(state="normal", text="▶ Start Auto-Apply")
        self._stop_btn.configure(state="disabled")
        self._client_box.configure(state="normal")
        msg = f"Done — {applied}/{total} submitted." if not self._stop_flag \
            else f"Stopped — {applied}/{total} done."
        self._status.configure(text=msg, text_color="#4CAF50")
        # Refresh quota/jobs view for the (possibly decremented) client.
        self._load_data()

    # ── CAPTCHA: human-in-the-loop ───────────────────────────────────────────
    def _captcha_prompt(self):
        self._captcha_btn.grid(row=1, column=0, columnspan=3, padx=4, pady=(6, 0), sticky="ew")
        self._captcha_btn.configure(state="normal", text="✅ I solved the CAPTCHA — Continue")
        self._status.configure(
            text="CAPTCHA — solve it in Chrome, then click Continue.", text_color="#FFB74D")
        try:
            self.lift(); self.focus_force(); self.bell()
        except Exception:
            pass

    def _continue_captcha(self):
        proc = self._proc
        if not proc or proc.poll() is not None or not proc.stdin:
            self._hide_captcha_btn(); return
        try:
            proc.stdin.write("\n"); proc.stdin.flush()
        except Exception as e:
            self._log_line(f"Could not resume: {e}")
        self._captcha_btn.configure(state="disabled", text="Resuming…")
        self._status.configure(text="Resuming…", text_color="#FFA500")

    def _hide_captcha_btn(self):
        try:
            self._captcha_btn.grid_remove()
        except Exception:
            pass

    def _log_line(self, text: str):
        self._log.insert("end", text + "\n")
        self._log.see("end")


if __name__ == "__main__":
    AutoApplyGUI().mainloop()
