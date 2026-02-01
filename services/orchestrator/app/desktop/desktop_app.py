from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import requests

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8787")
ADMIN_TOKEN = os.getenv("UI_ADMIN_TOKEN", os.getenv("ADMIN_TOKEN", "admin"))


def api_get(path: str):
    r = requests.get(f"{BACKEND_URL}{path}")
    r.raise_for_status()
    return r.json()


def api_post(path: str, json_payload=None):
    r = requests.post(f"{BACKEND_URL}{path}?token={ADMIN_TOKEN}", json=json_payload)
    r.raise_for_status()
    return r.json()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OpenAgent Workbench Desktop")
        self.geometry("1100x700")

        self.tasks = []
        self.selected_task_id = None

        self._build_ui()
        self.refresh_all()

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(top, text="Workspace ID").grid(row=0, column=0, sticky="w")
        self.ws_entry = ttk.Entry(top, width=28)
        self.ws_entry.grid(row=0, column=1, padx=6)

        ttk.Label(top, text="Skill ID").grid(row=0, column=2, sticky="w")
        self.skill_entry = ttk.Entry(top, width=28)
        self.skill_entry.grid(row=0, column=3, padx=6)

        ttk.Label(top, text="Mode").grid(row=0, column=4, sticky="w")
        self.mode_var = tk.StringVar(value="")
        self.mode_combo = ttk.Combobox(top, textvariable=self.mode_var, values=["", "fast", "pro"], width=8)
        self.mode_combo.grid(row=0, column=5, padx=6)

        ttk.Label(top, text="Goal").grid(row=1, column=0, sticky="w")
        self.goal_entry = ttk.Entry(top, width=90)
        self.goal_entry.grid(row=1, column=1, columnspan=5, sticky="we", padx=6, pady=6)

        btns = ttk.Frame(top)
        btns.grid(row=2, column=0, columnspan=6, sticky="w")
        ttk.Button(btns, text="Start Run", command=self.create_run).pack(side=tk.LEFT)
        ttk.Button(btns, text="Refresh", command=self.refresh_all).pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="Copy IDs (Workspaces/Skills)", command=self.show_ids).pack(side=tk.LEFT)

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=False)

        ttk.Label(left, text="Runs").pack(anchor="w")
        self.task_list = tk.Listbox(left, width=42, height=28)
        self.task_list.pack(fill=tk.BOTH, expand=True)
        self.task_list.bind("<<ListboxSelect>>", self.on_select_task)

        right = ttk.Frame(body)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10,0))

        ttk.Label(right, text="Run detail").pack(anchor="w")
        self.detail_text = tk.Text(right, height=22)
        self.detail_text.pack(fill=tk.BOTH, expand=True)

        self.approval_frame = ttk.Frame(right)
        self.approval_frame.pack(fill=tk.X, pady=8)

    def _set_detail(self, s: str):
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert(tk.END, s)

    def refresh_all(self):
        def worker():
            try:
                tasks = api_get("/api/tasks")
                self.tasks = tasks
                self.after(0, self._render_tasks)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def _render_tasks(self):
        self.task_list.delete(0, tk.END)
        for t in self.tasks:
            line = f"{t['status']:<16} {t['id'][:8]}  {t['mode']:<4}  {t['goal'][:40]}"
            self.task_list.insert(tk.END, line)

    def on_select_task(self, event=None):
        sel = self.task_list.curselection()
        if not sel:
            return
        idx = sel[0]
        task_id = self.tasks[idx]["id"]
        self.selected_task_id = task_id
        self.load_task_detail(task_id)

    def load_task_detail(self, task_id: str):
        def worker():
            try:
                detail = api_get(f"/api/tasks/{task_id}")
                self.after(0, lambda: self._render_detail(detail))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def _render_detail(self, detail):
        task = detail["task"]
        steps = detail["steps"]
        approvals = detail["approvals"]

        lines = []
        lines.append(f"ID: {task['id']}")
        lines.append(f"Status: {task['status']}")
        lines.append(f"Mode: {task['mode']}")
        lines.append(f"Workspace: {task['workspace_id']}")
        lines.append(f"Skill: {task['skill_id']}")
        lines.append("")
        lines.append("Goal:")
        lines.append(task["goal"])
        lines.append("")
        if task.get("output_path"):
            lines.append(f"Report: {task['output_path']}")
        if task.get("error"):
            lines.append("\nERROR:\n" + task["error"])

        lines.append("\nSteps:")
        for s in steps:
            lines.append(f"  {s['idx']+1:02d}. {s['status']:<16} {s['tool']}  {s['name']}")

        self._set_detail("\n".join(lines))

        # approvals UI
        for child in self.approval_frame.winfo_children():
            child.destroy()

        waiting = [s for s in steps if s["status"] == "waiting_approval"]
        if waiting:
            step = waiting[0]
            ttk.Label(self.approval_frame, text=f"Approval needed for step {step['idx']+1}: {step['tool']}").pack(anchor="w")
            reason_var = tk.StringVar(value="")
            reason_entry = ttk.Entry(self.approval_frame, textvariable=reason_var, width=80)
            reason_entry.pack(anchor="w", pady=4)

            def do(decision: str):
                try:
                    api_post(f"/api/tasks/{task_id}/approve/{step['id']}", {"decision": decision, "reason": reason_var.get()})
                    messagebox.showinfo("OK", f"{decision} sent. Run will resume if approved.")
                    self.load_task_detail(task_id)
                except Exception as e:
                    messagebox.showerror("Error", str(e))

            btns = ttk.Frame(self.approval_frame)
            btns.pack(anchor="w", pady=4)
            ttk.Button(btns, text="Approve", command=lambda: do("approve")).pack(side=tk.LEFT)
            ttk.Button(btns, text="Reject", command=lambda: do("reject")).pack(side=tk.LEFT, padx=8)
        else:
            ttk.Label(self.approval_frame, text="No approvals pending.").pack(anchor="w")

    def create_run(self):
        ws = self.ws_entry.get().strip()
        skill = self.skill_entry.get().strip()
        goal = self.goal_entry.get().strip()
        mode = self.mode_var.get().strip() or None
        if not ws or not skill or not goal:
            messagebox.showwarning("Missing", "workspace_id, skill_id, and goal are required.")
            return
        payload = {"workspace_id": ws, "skill_id": skill, "goal": goal, "mode": mode}
        def worker():
            try:
                res = api_post("/api/tasks", payload)
                task_id = res["task_id"]
                self.after(0, lambda: messagebox.showinfo("Started", f"Task: {task_id}"))
                self.after(0, self.refresh_all)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def show_ids(self):
        def worker():
            try:
                ws = api_get("/api/workspaces")
                skills = api_get("/api/skills")
                msg = "Workspaces:\n" + "\n".join([f"- {w['name']}: {w['id']}" for w in ws]) + "\n\nSkills:\n" + "\n".join([f"- {s['name']}: {s['id']}" for s in skills])
                self.after(0, lambda: messagebox.showinfo("IDs", msg))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
