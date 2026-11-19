"""The developer agent for interfaces without a Tk window (the web workspace).

It runs the same agent (coding_agent.run_agent) under the same rules as the
desktop window:

- Only in a project folder already chosen for a developer chat in the
  desktop app; this service never accepts a new folder.
- Every file change and command waits for a person (app_core.approvals):
  declined unless approved, and a command needs its second check and the full
  wait before it can be approved. ``answer`` refuses anything earlier.
- Every change is saved for undo first (app_core.checkpoints).
- Stop, or a closed page, declines whatever is waiting and stops the agent.

Events for the interface: ``user``, ``agent`` (each step), ``approval`` and
``approval_closed`` (a review), then exactly one ``done`` or ``error``.
"""

import math
from pathlib import Path
import secrets
import threading

from app_core import activity, approvals, settings
from app_core.chat_service import MAX_MESSAGE_CHARS, ServiceError, _Emitter
from app_core.checkpoints import CheckpointError, CheckpointStore
from app_core.conversation import agent_messages, developer_system_prompt
from chat_store import ChatStoreError
from coding_agent import AgentCancelled, run_agent

MAX_REVIEW_CHARS = 200_000
NO_FINAL_MESSAGE = "The agent finished without a final message. Check Activity for its results."


def review_view(action):
    """What a person needs to review an action. The full new file content is not
    sent: the diff shows the change, and for a new file the whole file."""
    view = {key: action[key] for key in ("kind", "path", "exists", "destination", "command", "cwd", "description",
                                         "timeout_seconds") if key in action}
    if isinstance(action.get("diff"), str):
        view["diff"] = action["diff"][:MAX_REVIEW_CHARS]
        view["diff_shortened"] = len(action["diff"]) > MAX_REVIEW_CHARS
    return view


class AgentService:
    confirm_seconds = approvals.COMMAND_CONFIRM_SECONDS

    def __init__(self, chats, checkpoints=None):
        self.chats = chats
        self.store = chats.store
        self.checkpoints = checkpoints or CheckpointStore(self.store.directory / "checkpoints")
        self.pending = {}
        self.lock = threading.Lock()

    # ------------------------------------------------------------ tasks

    def _developer_chat(self, chat_id):
        record = self.chats.get(chat_id)
        if record["mode"] != "dev":
            raise ServiceError("This is an ordinary chat. The developer agent works in developer chats.", 409)
        project = record.get("project_path") or ""
        if not project or not Path(project).is_dir():
            raise ServiceError("This developer chat has no project folder that exists. Choose the folder in the "
                               "desktop app (Developer > Open project).", 409)
        return record

    def prepare(self, chat_id, text, model, backend):
        """Validate a task before anything streams; reserves the chat (``run`` or ``release`` frees it)."""
        if not isinstance(text, str) or not text.strip():
            raise ServiceError("Describe the change first.")
        if len(text) > MAX_MESSAGE_CHARS:
            raise ServiceError(f"Requests are limited to {MAX_MESSAGE_CHARS:,} characters.")
        if not isinstance(model, str) or not model.strip():
            raise ServiceError("Choose a model first.")
        record = self._developer_chat(chat_id)
        if record.get("backend", "Ollama") != backend and record["messages"]:
            raise ServiceError(f"This chat was written with {record.get('backend', 'Ollama')}. "
                               "Switch the engine to it in Settings to continue it.", 409)
        with self.chats.lock:
            if chat_id in self.chats.active:
                raise ServiceError("This chat is already working.", 409)
            self.chats.active.add(chat_id)
        return record

    def release(self, chat_id):
        self.chats.release(chat_id)

    def create_task(self, chat_id, backend):
        """A new developer chat for the same project folder (a fresh task)."""
        source = self._developer_chat(chat_id)
        with self.chats.lock:
            record = self.store.new_record(model=source.get("model", ""), mode="dev", project_path=source["project_path"])
            record["backend"] = backend
            saved = self.store.save(record)
            self.chats._remember(saved)
            return saved

    def run(self, chat_id, text, model, backend, on_event, cancel_event, context_length=None):
        """Run one task; ``prepare`` must have succeeded for this chat."""
        emit = _Emitter(on_event)
        state = {"activity": None, "decisions": []}
        try:
            self._run(chat_id, text.strip(), model, backend, emit, cancel_event, context_length, state)
        except AgentCancelled:
            self._keep_activity(chat_id, state)
            emit({"type": "done", "chat": self.chats.get(chat_id), "stopped": True}, terminal=True)
        except ServiceError as error:
            self._keep_activity(chat_id, state)
            emit({"type": "error", "error": str(error)}, terminal=True)
        except (RuntimeError, ValueError, OSError) as error:
            self._keep_activity(chat_id, state)
            emit({"type": "error", "error": str(error)}, terminal=True)
        finally:
            # Nothing may wait for an answer once the task is over.
            for decision in state["decisions"]:
                approvals.resolve(decision, False)
            self.chats.release(chat_id)

    def _keep_activity(self, chat_id, state):
        if state["activity"] is None:
            return
        try:
            with self.chats.lock:
                latest = self.chats.get(chat_id)
                latest["activity"] = state["activity"]
                self.chats._save(latest)
        except (ServiceError, ChatStoreError, OSError):
            pass

    def _run(self, chat_id, text, model, backend, emit, cancel_event, context_length, state):
        with self.chats.lock:
            record = self._developer_chat(chat_id)
            record["messages"].append({"role": "user", "content": text})
            record["activity"] = activity.append(record.get("activity", []),
                                                 {"type": "request_start", "text": "Developer task started."})
            record.update(model=model, backend=backend, draft="")
            if context_length is not None:
                record["context_length"] = context_length
            record = self.chats._save(record)
            others = self.chats.records()
        state["activity"] = list(record["activity"])
        emit({"type": "user", "message": record["messages"][-1]})
        provider = self.chats.provider_factory()
        if provider.name == "Ollama":
            emit({"type": "agent", "event": {"type": "status", "text": "Checking that the model can use tools..."}})
            try:
                details = provider.model_details(model)
            except (RuntimeError, ValueError, OSError) as error:
                raise ServiceError(str(error), 503) from None
            if "tools" not in (details.get("capabilities") or []):
                raise ServiceError("This model does not advertise tool calling. Choose a model that can use tools, "
                                   "such as qwen3-coder.", 409)
        project = record["project_path"]
        memory = self.chats.shared_memory
        shared = memory.context(text, others, chat_id) if memory else ""
        system = developer_system_prompt(project, "", "", record["activity"])
        messages = agent_messages(record["messages"], record.get("agent_history", []), text, system, shared)
        checkpoint = self.checkpoints.begin(chat_id, project)

        def on_agent_event(event):
            state["activity"] = activity.append(state["activity"], event)
            emit({"type": "agent", "event": activity.activity_record(event)})

        def approve(action):
            decision = approvals.new_decision(action)
            decision_id = secrets.token_hex(8)
            with self.lock:
                self.pending[decision_id] = decision
            state["decisions"].append(decision)
            try:
                answer = approvals.wait_for(decision, lambda _: emit(
                    {"type": "approval", "id": decision_id, "action": review_view(action)}), cancel_event)
            finally:
                with self.lock:
                    self.pending.pop(decision_id, None)
            emit({"type": "approval_closed", "id": decision_id, "approved": bool(decision["approved"])})
            return answer

        host, chat_fn = provider.agent_backend(cancel_event)
        context = context_length or record.get("context_length") or 4096
        result = run_agent(host, model, project, messages, on_agent_event, approve, cancel_event=cancel_event,
                           context_length=context, chat_fn=chat_fn, checkpoint=checkpoint,
                           file_limit=settings.load_file_limit(self.store.directory / "agent.settings"))
        with self.chats.lock:
            latest = self.chats.get(chat_id)
            latest["messages"].append({"role": "assistant", "content": result.get("assistant_text") or NO_FINAL_MESSAGE})
            latest["agent_history"] = result["messages"]
            latest["activity"] = state["activity"]
            saved = self.chats._save(latest)
        emit({"type": "done", "chat": saved, "stopped": False, "changed": len(checkpoint.manifest["files"])},
             terminal=True)

    # ------------------------------------------------------------ reviews

    def answer(self, decision_id, choice):
        """A person's answer to a waiting review: "reject", "second_check" (commands) or "approve"."""
        with self.lock:
            decision = self.pending.get(decision_id)
        if decision is None:
            raise ServiceError("That review is no longer open.", 404)
        if choice not in ("reject", "second_check", "approve"):
            raise ServiceError("Choose approve, second_check or reject.")
        if decision["event"].is_set():
            # The first answer counts, from this page or another one.
            raise ServiceError("That review was already answered.", 409)
        if choice == "reject":
            if not approvals.resolve(decision, False):
                raise ServiceError("That review was already answered.", 409)
            return {"closed": True}
        if choice == "second_check":
            if not approvals.is_command(decision):
                raise ServiceError("Only commands have a second check.")
            approvals.begin_second_check(decision, self.confirm_seconds)
            return {"closed": False, "seconds_left": approvals.seconds_left(decision)}
        if approvals.resolve(decision, True):
            return {"closed": True}
        left = approvals.seconds_left(decision)
        raise ServiceError("That review was already answered." if decision["event"].is_set()
                           else "Confirm the command a second time first." if left is None
                           else f"Wait {max(1, math.ceil(left))} more second{'s' if left > 1 else ''}, then confirm.", 409)

    # ------------------------------------------------------------ undo

    def undo_available(self, chat_id):
        try:
            return self.checkpoints.latest_undoable(chat_id) is not None
        except OSError:
            return False

    def undo(self, chat_id):
        self._developer_chat(chat_id)
        point = self.checkpoints.latest_undoable(chat_id)
        if point is None:
            raise ServiceError("There are no AI file changes to undo in this chat.", 404)
        with self.chats.lock:
            if chat_id in self.chats.active:
                raise ServiceError("Wait for the agent to finish, or stop it, before undoing.", 409)
            try:
                report = self.checkpoints.undo(point["id"])
            except CheckpointError as error:
                raise ServiceError(str(error), 409) from None
            done = len(report["restored"]) + len(report["removed"])
            lines = [f"Undid the AI's changes to {done} file{'s' if done != 1 else ''}."]
            lines += [f"Restored: {path}" for path in report["restored"]]
            lines += [f"Removed (created by the AI): {path}" for path in report["removed"]]
            lines += [f"Left as it is ({reason}): {path}" for path, reason in report["kept"]]
            latest = self.chats.get(chat_id)
            latest["activity"] = activity.append(latest.get("activity", []), {"type": "undone", "text": "\n".join(lines)})
            self.chats._save(latest)
        return {"report": {"restored": report["restored"], "removed": report["removed"],
                           "kept": [{"path": path, "reason": reason} for path, reason in report["kept"]]},
                "message": lines[0] + (f" {len(report['kept'])} left as it is." if report["kept"] else "")}
