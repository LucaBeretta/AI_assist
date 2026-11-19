"""Developer workers with JSON approval rendezvous and no UI references."""

from copy import deepcopy
import threading
import uuid

import coding_agent
from webui.model_loading import ModelLoadProgress


class DeveloperTasks:
    def __init__(self, bridge, save_result, running_details):
        self.bridge = bridge
        self.save_result = save_result
        self.running_details = running_details
        self.lock = threading.RLock()
        self.tasks = {}
        self.closed = False

    def busy(self, chat_id):
        with self.lock:
            return any(task["chat_id"] == chat_id and not task["finished"].is_set() for task in self.tasks.values())

    def context_usage(self, chat_id):
        with self.lock:
            return next((deepcopy(task["context"]) for task in self.tasks.values()
                         if task["chat_id"] == chat_id and "context" in task), None)

    def uses_model(self, name):
        with self.lock:
            return any(task["record"]["model"] == name and not task["finished"].is_set() for task in self.tasks.values())

    def uses_colibri(self):
        with self.lock:
            return any(task.get("hybrid") and not task["finished"].is_set() for task in self.tasks.values())

    def manages_model(self, name):
        with self.lock:
            return any(task.get("manages_memory") and task["record"]["model"] == name
                       and not task["finished"].is_set() for task in self.tasks.values())

    def status(self, chat_id):
        with self.lock:
            task = next((item for item in self.tasks.values() if item["chat_id"] == chat_id), None)
            return deepcopy(task.get("status", {})) if task else {}

    def uses_project(self, project):
        with self.lock:
            return any(task["record"]["project_path"] == str(project) and not task["finished"].is_set() for task in self.tasks.values())

    def start(self, chat_id, record, **options):
        with self.lock:
            if self.closed or self.busy(chat_id):
                raise ValueError("This Developer chat is busy or closing.")
            identity = str(uuid.uuid4())
            options["task_id"] = identity
            task = {"chat_id": chat_id, "record": deepcopy(record), "cancel": threading.Event(),
                    "finished": threading.Event(), "pending": {},
                    "hybrid": bool(options.get("hybrid")),
                    "manages_memory": bool((options.get("hybrid") or {}).get("manage_memory")),
                    "status": {"active": True, "step": 0, "max_steps": options.get("max_steps", 20), "metrics": {}}}
            self.tasks[identity] = task
            thread = threading.Thread(target=self._run, args=(identity, task, options), daemon=True,
                                      name="webui-developer-" + identity[:8])
            try:
                thread.start()
            except Exception:
                del self.tasks[identity]
                raise
        return {"ok": True, "task_id": identity}

    def _wait(self, identity, task, kind, value):
        approval_id = str(uuid.uuid4())
        pending = {"kind": kind, "event": threading.Event()}
        if kind == "action":
            pending["action"] = deepcopy(value["action"])
        with self.lock:
            task["pending"][approval_id] = pending
        self.bridge.emit("plan_approval" if kind == "plan" else "approval", task_id=identity,
                         chat_id=task["chat_id"], approval_id=approval_id, **value)
        try:
            while not pending["event"].wait(0.05):
                if task["cancel"].is_set():
                    raise coding_agent.AgentCancelled("Developer task cancelled while awaiting approval.")
            if task["cancel"].is_set():
                raise coding_agent.AgentCancelled("Developer task cancelled.")
            return pending["decision"]
        finally:
            with self.lock:
                task["pending"].pop(approval_id, None)

    def approve_plan(self, identity, decision, text=None):
        if decision not in ("approve", "edit", "cancel"):
            raise ValueError("Plan decision must be approve, edit or cancel.")
        if decision == "edit" and (not isinstance(text, str) or not text.strip() or len(text) > 50000):
            raise ValueError("The edited plan must be nonempty text up to 50,000 characters.")
        with self.lock:
            task = self.tasks.get(identity)
            if task is None or task["finished"].is_set():
                raise ValueError("This task is no longer awaiting a plan.")
            match = next((item for item in task["pending"].values() if item["kind"] == "plan" and not item["event"].is_set()), None)
            if match is None:
                raise ValueError("This task is not awaiting plan approval.")
            match["decision"] = True if decision == "approve" else text if decision == "edit" else False
            match["event"].set()
        return {"ok": True}

    def approve_action(self, identity, approval_id, approved, hunks=None):
        if type(approved) is not bool:
            raise ValueError("Action approval must be true or false.")
        with self.lock:
            task = self.tasks.get(identity)
            pending = task["pending"].get(approval_id) if task else None
            if pending is None or pending["kind"] != "action" or pending["event"].is_set():
                raise ValueError("This action approval is no longer pending.")
            if hunks is not None:
                import diff_hunks
                if pending["action"]["kind"] != "write_file":
                    raise ValueError("Hunk selection is available only for file writes.")
                diff_hunks.validate_selection(pending["action"]["hunks"], hunks)
            pending["decision"] = {"approved": True, "hunks": hunks} if approved and hunks is not None else approved
            pending["event"].set()
        return {"ok": True}

    def stop(self, identity):
        with self.lock:
            task = self.tasks.get(identity)
            active = task is not None and not task["finished"].is_set()
            if active:
                task["cancel"].set()
                if "loading" in task:
                    task["loading"].close()
        return {"ok": True, "task_id": identity, "active": active}

    def _run(self, identity, task, options):
        options = dict(options)
        hybrid = options.pop("hybrid", None)
        loading = ModelLoadProgress(self.bridge.emit, options["model"], task["cancel"],
                                    task_id=identity, chat_id=task["chat_id"])
        with self.lock:
            task["loading"] = loading
        def emit(event):
            event = deepcopy(event)
            kind = event.pop("type")
            event.pop("task_id", None)
            with self.lock:
                if type(event.get("step")) is int:
                    task["status"]["step"] = event["step"]
                if kind == "metrics" and isinstance(event.get("metrics"), dict):
                    task["status"]["metrics"] = deepcopy(event["metrics"])
            if kind in {"assistant", "tool_start", "metrics", "plan"}:
                loading.first_output()
            self.bridge.emit(kind, task_id=identity, chat_id=task["chat_id"], **event)
        def context(value):
            with self.lock:
                task["context"] = deepcopy(value)
            self.bridge.emit("context_usage", task_id=identity, chat_id=task["chat_id"], **value)
        try:
            loading.start(self.running_details)
            def worker(messages, events):
                return coding_agent.run_agent(
                    on_event=events, on_context=context, cancel_event=task["cancel"],
                    approve_action=lambda action: self._wait(identity, task, "action", {"action": action}),
                    approve_plan=lambda text: self._wait(identity, task, "plan", {"text": text}),
                    **{**options, "messages": messages})
            if hybrid:
                from hybrid_engine import run_hybrid
                from hybrid_ui import memory_callbacks
                from colibri_backend import ColibriClient
                state, before, prepare, cleanup = memory_callbacks(hybrid, options["host"], options["model"], task["cancel"])
                result = run_hybrid(worker,
                    lambda messages: ColibriClient(state["url"], hybrid["key"]).review(messages, hybrid["review_model"], task["cancel"]),
                    options["messages"], options["project_root"], emit, task["cancel"],
                    before_worker=before, before_review=prepare, after_review=cleanup, max_file_bytes=options["file_limit"])
            else:
                result = worker(options["messages"], emit)
            if task["cancel"].is_set() and not hybrid:
                raise coding_agent.AgentCancelled("Developer task cancelled.")
            self.save_result(task["record"], {**result, "stopped": task["cancel"].is_set(), "metrics": deepcopy(task["status"]["metrics"])})
            kind, extra = "agent_stopped" if task["cancel"].is_set() else "agent_done", {"text": result["assistant_text"]}
        except coding_agent.AgentCancelled as error:
            kind, extra = "agent_stopped", {"text": str(error)}
        except Exception as error:
            kind, extra = "agent_error", {"error": str(error)}
        loading.close()
        with self.lock:
            task["finished"].set()
            task["pending"].clear()
            self.tasks.pop(identity, None)
        self.bridge.emit(kind, task_id=identity, chat_id=task["chat_id"], **extra)

    def close(self):
        with self.lock:
            self.closed = True
            tasks = list(self.tasks.values())
            for task in tasks:
                task["cancel"].set()
                if "loading" in task:
                    task["loading"].close()
            return [task["finished"] for task in tasks]
