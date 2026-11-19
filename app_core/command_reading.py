"""What a developer-agent command does, in plain words, worked out on this computer.

The person approving a command sees the command itself and, from here, AI
Assist's own reading of it: each program and what it is known to do, the
files and web addresses it names, and warnings for what deserves a second
look (hidden commands, administrator rights, system changes, deleting, going
online, installing, paths outside the project, unchecked scripts, unknown
programs).

This is a help for reading, never a permission. It cannot prove a command is
harmless and never says so; it does not shorten or skip a confirmation; a
program it does not know is reported as unknown; and if the command cannot
be read, that is said too. The model's own description is shown separately,
because a model can be wrong or misled.
"""

import ntpath
import posixpath
import re

NOTE = "AI Assist's own reading of the command. It helps you check it, but it cannot prove a command is harmless."
MAX_STEPS = 12
MAX_FILES = 12
MAX_ADDRESSES = 6
_DEPTH = 3

# Warnings, most serious first.
_ORDER = ("hidden", "admin", "system", "delete", "online", "install", "outside", "script", "unknown")

_NULLS = {"$null", "/dev/null", "nul", "null"}
# PowerShell comparison and logic operators, and assignment: `$a -eq 1` is a value, not a program.
_OPERATOR = re.compile(r"(?i)-(eq|ne|gt|ge|lt|le|like|notlike|match|notmatch|contains|notcontains|in|notin|replace|"
                       r"split|join|is|isnot|as|f|and|or|not|xor|band|bor|bxor|shl|shr|c?eq|ieq)|[+\-*/]?=|\.\w+.*|\[.*")
# Flags whose next word is a value, not a file.
_VALUE_FLAGS = {"-value", "-encoding", "-pattern", "-filter", "-include", "-exclude", "-itemtype", "-type", "-message",
                "-inputobject", "-delimiter", "-depth", "-first", "-last", "-skip", "-property", "-erroraction",
                "-warningaction", "-method", "-body", "-headers", "-contenttype", "-useragent", "-argumentlist", "-verb",
                "-windowstyle", "-scope", "-m", "--message", "-e", "--regexp", "-replace", "-separator",
                "-stream", "-credential", "-timeoutsec", "-maximumredirection", "-sessionvariable"}
# Values that are never file names: permission modes, numbers.
_VALUE = re.compile(r"[+-]?[0-7]{3,4}|[ugoa]*[+\-=][rwxXst]+|[+-]?\d+(\.\d+)?[smhdkKMG%]?")
_URL = re.compile(r"(?i)\b(?:https?|ftp|ssh|git)://[^\s'\"<>|]+|\b[\w.-]+@[\w.-]+:[\w./~-]+")

# Words that start a loop, condition or block: the commands inside are read on their own.
_KEYWORDS_DROP = {"if", "elif", "elseif", "while", "until", "then", "else", "do", "!", "try", "finally", "begin", "process", "end"}
_KEYWORDS_SKIP = {"for", "foreach", "case", "esac", "function", "switch", "param", "done", "fi", "in", "catch", "return", "break", "continue"}

# program: (kind, what it does). Kinds: read, write, copy, move, delete, online, install, system, admin, hidden, wait.
_PROGRAMS = {}


def _add(kind, text, *names):
    for name in names:
        _PROGRAMS[name] = (kind, text)


_add("read", "lists files", "ls", "dir", "get-childitem", "gci", "tree")
_add("read", "shows a file's contents", "cat", "type", "get-content", "gc", "less", "more", "bat")
_add("read", "shows the start or end of a file", "head", "tail")
_add("read", "searches text in files", "grep", "egrep", "rg", "ag", "findstr", "select-string", "sls")
_add("read", "counts lines, words or items", "wc", "measure-object", "measure")
_add("read", "compares files", "diff", "fc", "cmp", "compare-object")
_add("read", "shows the current folder", "pwd", "get-location", "gl")
_add("read", "finds where a program is installed", "which", "where", "where.exe", "get-command", "gcm", "command", "type-of")
_add("read", "checks a file or shows its details", "test-path", "get-item", "gi", "stat", "file", "du", "df", "get-filehash", "sha256sum", "md5sum")
_add("read", "prints text", "echo", "write-output", "write", "write-host", "printf", "print")
_add("read", "sorts, filters or formats the output", "sort", "sort-object", "uniq", "select-object", "select", "where-object", "?",
     "foreach-object", "%", "format-table", "ft", "format-list", "fl", "out-string", "out-host", "out-null", "convertto-json",
     "convertfrom-json", "jq", "cut", "tr", "awk", "column", "group-object", "join-string", "get-unique", "split-path", "join-path",
     "resolve-path", "convert-path")
_add("read", "shows information about this computer", "get-date", "date", "hostname", "whoami", "env", "printenv", "uname",
     "get-process", "ps", "tasklist", "systeminfo", "ver", "get-host", "$psversiontable", "nproc", "free", "uptime", "id", "lsb_release")
_add("read", "changes nothing (a shell word)", "true", "false", "exit", "cls", "clear", "set-strictmode", "set", "export", "local",
     "set-variable", "get-variable", "test", "[", "[[", "read", "shift", "trap", "wait", "history", "alias", "unset", "declare", "readonly")
_add("read", "changes the current folder", "cd", "chdir", "set-location", "sl", "pushd", "popd", "push-location", "pop-location")
_add("wait", "waits", "sleep", "start-sleep")
_add("write", "creates a folder", "mkdir", "md")
_add("write", "creates a file or folder", "new-item", "ni", "touch")
_add("write", "writes to a file", "set-content", "out-file", "tee", "tee-object", "export-csv")
_add("write", "adds text to a file", "add-content", "ac")
_add("write", "packs or unpacks files", "tar", "zip", "unzip", "gzip", "gunzip", "7z", "expand-archive", "compress-archive")
_add("write", "creates a link to a file", "ln", "mklink")
_add("write", "changes who may use a file", "chmod", "chown", "chgrp", "icacls", "attrib", "takeown")
_add("write", "changes text in a file (sed)", "sed")
_add("copy", "copies files", "cp", "copy", "copy-item", "cpi", "xcopy", "robocopy")
_add("move", "moves or renames files", "mv", "move", "move-item", "mi", "ren", "rename", "rename-item", "rni")
_add("delete", "deletes files", "rm", "del", "erase", "remove-item", "ri", "unlink", "shred", "rimraf", "trash")
_add("delete", "deletes a folder", "rmdir", "rd")
_add("delete", "empties a file", "clear-content", "clc", "truncate")
_add("online", "downloads from, or sends to, a web address", "curl", "curl.exe", "wget", "invoke-webrequest", "iwr",
     "invoke-restmethod", "irm", "start-bitstransfer", "bitsadmin", "aria2c", "http", "xh")
_add("online", "connects to another computer", "ssh", "telnet", "nc", "ncat", "netcat", "socat", "mosh", "enter-pssession", "new-pssession")
_add("online", "copies files to or from another computer", "scp", "sftp", "ftp", "rsync")
_add("online", "checks or looks up another computer on the network", "ping", "test-netconnection", "tnc", "nslookup", "dig",
     "resolve-dnsname", "traceroute", "tracert", "host")
_add("online", "sends an email", "send-mailmessage", "mail", "sendmail")
_add("online", "works with an online service", "gh", "az", "aws", "gcloud", "vercel", "netlify", "heroku", "firebase", "fly",
     "huggingface-cli", "hf", "kubectl", "terraform", "wrangler", "supabase")
_add("install", "installs software", "winget", "choco", "scoop", "apt", "apt-get", "brew", "dnf", "yum", "pacman", "snap",
     "msiexec", "install-module", "install-package", "flatpak", "zypper", "apk", "port", "gem", "cpan", "composer")
_add("admin", "runs the rest with administrator rights", "sudo", "doas", "runas", "su", "gsudo")
_add("system", "turns off or restarts the computer", "shutdown", "restart-computer", "stop-computer", "reboot", "halt", "poweroff")
_add("system", "stops running programs", "taskkill", "kill", "stop-process", "spps", "pkill", "killall")
_add("system", "changes the Windows registry or other settings", "reg", "reg.exe", "regedit", "set-itemproperty", "sp",
     "new-itemproperty", "remove-itemproperty", "defaults")
_add("system", "schedules a program to run later", "schtasks", "register-scheduledtask", "crontab", "at", "new-scheduledtask")
_add("system", "changes which scripts PowerShell may run", "set-executionpolicy", "unblock-file")
_add("system", "changes network or firewall settings", "netsh", "new-netfirewallrule", "set-netfirewallprofile",
     "set-netfirewallrule", "remove-netfirewallrule", "iptables", "ufw", "pfctl", "route", "ifconfig", "ip")
_add("system", "changes environment settings permanently", "setx", "[environment]::setenvironmentvariable")
_add("system", "works directly on disks", "format", "diskpart", "mkfs", "fdisk", "dd", "mount", "umount", "bcdedit", "vssadmin",
     "cipher", "format-volume", "clear-disk", "initialize-disk")
_add("system", "manages services or users", "sc.exe", "systemctl", "service", "launchctl", "new-service", "set-service",
     "stop-service", "start-service", "restart-service", "net", "net.exe", "useradd", "usermod", "userdel", "passwd",
     "new-localuser", "add-localgroupmember", "wmic", "dism", "sfc", "gpupdate")
_add("hidden", "runs text as a command", "invoke-expression", "iex", "eval")
_add("hidden", "runs another script inside this shell", "source", ".")
_add("hidden", "runs a block of commands, possibly on another computer", "invoke-command", "icm", "start-job", "add-type")

_SHELLS = {"powershell", "powershell.exe", "pwsh", "pwsh.exe", "cmd", "cmd.exe", "bash", "sh", "zsh", "dash", "ksh", "fish", "wsl", "wsl.exe"}
_WRAPPERS = {"time", "nohup", "nice", "exec", "builtin", "stdbuf", "caffeinate", "watch", "xargs"}
_STARTERS = {"start-process", "saps", "start", "invoke-item", "ii", "explorer", "explorer.exe", "open", "xdg-open"}
_SCRIPTS = (".sh", ".bash", ".ps1", ".psm1", ".bat", ".cmd", ".vbs", ".js", ".mjs", ".py", ".rb", ".pl", ".exe", ".msi", ".com")
_PACKAGE_MANAGERS = {"npm", "pnpm", "yarn", "bun"}
_PYTHON_MANAGERS = {"pip", "uv", "poetry", "conda", "mamba", "pipx", "pipenv", "pdm", "hatch"}
_BUILD_TOOLS = {
    "cargo": "Rust's build tool", "go": "Go's tool", "dotnet": ".NET's tool", "mvn": "Maven (Java builds)",
    "gradle": "Gradle (Java builds)", "gradlew": "the project's Gradle wrapper", "mvnw": "the project's Maven wrapper",
    "deno": "Deno (JavaScript)", "swift": "Swift's tool", "flutter": "Flutter's tool", "dart": "Dart's tool",
}
_CHECKERS = {
    "pytest": "runs the tests", "jest": "runs the tests", "vitest": "runs the tests", "mocha": "runs the tests",
    "tox": "runs the tests in fresh environments", "nox": "runs the tests in fresh environments",
    "mypy": "checks the code", "flake8": "checks the code", "pylint": "checks the code", "pyflakes": "checks the code",
    "eslint": "checks the code", "tsc": "checks and compiles TypeScript", "javac": "compiles Java", "rustc": "compiles Rust",
    "gcc": "compiles C", "g++": "compiles C++", "clang": "compiles C", "clang++": "compiles C++", "cl": "compiles C or C++",
    "cmake": "prepares or runs a C/C++ build", "ninja": "runs a build", "msbuild": "runs a build",
    "java": "runs a Java program", "ruby": "runs a Ruby program", "perl": "runs a Perl program", "php": "runs a PHP program",
    "black": "formats Python code (rewrites files)", "isort": "sorts Python imports (rewrites files)",
    "autopep8": "formats Python code", "prettier": "formats code", "rustfmt": "formats Rust code (rewrites files)",
    "gofmt": "formats Go code", "clang-format": "formats C code", "ruff": "checks or formats Python code",
    "code": "opens Visual Studio Code", "notepad": "opens Notepad", "vim": "opens a text editor", "nano": "opens a text editor",
}
_FORMATTERS = {"black", "isort", "rustfmt"}


def read_command(command, cwd, windows=None):
    """AI Assist's reading of ``command``, run in folder ``cwd``.

    Returns ``{"steps": [text], "warnings": [{"kind", "text"}], "files": [{"path",
    "effect", "inside", "exists"}], "addresses": [text], "note": NOTE}``. It never
    raises: a command it cannot read gets a warning that says so."""
    import os
    windows = os.name == "nt" if windows is None else windows
    reading = _Reading(str(cwd or ""), windows)
    try:
        reading.command(str(command or ""), 0)
    except Exception:
        reading.warn("unknown", "AI Assist could not read this command. Check every part of it yourself.")
    return reading.result()


def summary_lines(reading):
    """The reading as plain text lines, for interfaces without rich layout."""
    lines = [warning["text"] for warning in reading["warnings"]]
    lines += ["What it does:"] + [f"  - {step}" for step in reading["steps"]] if reading["steps"] else []
    if reading["files"]:
        lines.append("Files: " + ", ".join(f"{item['path']} ({item['effect']})" for item in reading["files"]))
    if reading["addresses"]:
        lines.append("Addresses: " + ", ".join(reading["addresses"]))
    return lines


class _Reading:
    def __init__(self, cwd, windows):
        self.windows = windows
        self.paths = ntpath if windows else posixpath
        self.root = self.paths.normpath(cwd) if cwd else ""
        self.steps, self.warnings, self.addresses = [], [], []
        self.files = {}
        self.extra_steps = 0

    # ---------------------------------------------------------------- results
    def step(self, text):
        if text in self.steps:
            return
        if len(self.steps) < MAX_STEPS:
            self.steps.append(text)
        else:
            self.extra_steps += 1

    def warn(self, kind, text):
        if not any(item["text"] == text for item in self.warnings):
            self.warnings.append({"kind": kind, "text": text})

    def result(self):
        steps = list(self.steps)
        if self.extra_steps:
            steps.append(f"…and {self.extra_steps} more step{'s' if self.extra_steps > 1 else ''}.")
        warnings = sorted(self.warnings, key=lambda item: _ORDER.index(item["kind"]))
        return {"steps": steps, "warnings": warnings, "files": list(self.files.values())[:MAX_FILES],
                "addresses": self.addresses[:MAX_ADDRESSES], "note": NOTE}

    # ---------------------------------------------------------------- parsing
    def command(self, text, depth):
        if depth > _DEPTH:
            self.warn("hidden", "Commands are nested too deeply for AI Assist to read. Check the inner commands yourself.")
            return
        for match in _URL.finditer(text):
            address = match.group(0).rstrip(".,;)")
            if address not in self.addresses:
                self.addresses.append(address)
        if re.search(r"(?i)\b(powershell|pwsh)(\.exe)?\b[^|;&\n]*\s-(e|ec|en|enc|enco\w*)\s", text + " "):
            self.warn("hidden", "Contains an encoded PowerShell command that AI Assist cannot read.")
        if re.search(r"(?i)frombase64string|base64\s+(-d|--decode)|certutil\s+-decode", text):
            self.warn("hidden", "Decodes hidden (base64) content, which AI Assist cannot read.")
        for segment in _segments(text, self.windows):
            self.segment(segment, depth)
        if self.addresses and not any(item["kind"] == "online" for item in self.warnings):
            self.warn("online", f"Names a web address ({self.addresses[0]}), so it may go online.")

    def segment(self, tokens, depth):
        plain = [(text, quoted) for text, redirect, quoted in tokens if not redirect]
        for target, redirect, _ in tokens:
            if redirect and target.lower() not in _NULLS:
                self.path(target, "written" if redirect == "write" else "used")
        # Leading variable assignments: NAME=value (POSIX) or $name = (PowerShell).
        while plain and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", plain[0][0]) and not self.windows:
            plain.pop(0)
        if len(plain) >= 2 and plain[0][0].startswith("$") and plain[1][0] in ("=", "+=", "-=", "*=", "/="):
            plain = plain[2:]
            if plain and plain[0][1]:
                return  # a quoted value is stored, not run
        words = [text for text, _ in plain]
        while words and words[0].lower() in _KEYWORDS_DROP:
            words.pop(0)
        if not words or words[0].lower() in _KEYWORDS_SKIP:
            return
        first = words[0]
        if first.lower() not in _PROGRAMS and (first[0] in "-$@[=+*" or re.fullmatch(r"[\d.,]+", first)):
            if first.startswith("$") and not first.startswith("$_") and len(words) > 1 \
                    and not _OPERATOR.fullmatch(words[1]):
                # `& $tool args` or `$CMD args`: the program's name is only known when it runs.
                self.step(f"{first}: runs a program named by a variable.")
                self.warn("hidden", f"Runs a program named by a variable ({first}), which AI Assist cannot read.")
            return  # otherwise an operator, value or variable: nothing runs here
        self.program(first, words[1:], depth)

    def program(self, token, args, depth):
        name = _program_name(token)
        lowered = token.lower()
        if lowered in _PROGRAMS:
            name = lowered
        script = self.script_path(token, name)
        if script:
            self.path(token, "used")
            self.step(f"{token}: runs a script or program file from this folder.")
            self.warn("script", f"Runs {token}, a file whose content AI Assist did not check.")
            return
        if name in _SHELLS:
            return self.shell(name, args, depth)
        if name in ("sudo", "doas", "gsudo", "runas"):
            self.step(f"{name}: runs the rest with administrator rights.")
            self.warn("admin", f"Asks for administrator rights ({name}).")
            if name == "runas":
                inner = [arg for arg in args if not _is_flag(arg, True)]
                if inner:
                    self.command(inner[-1], depth + 1)
                return
            rest = list(args)
            while rest and rest[0].startswith("-"):
                flag = rest.pop(0)
                if flag in ("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U") and rest:
                    rest.pop(0)
            if rest:
                self.program(rest[0], rest[1:], depth + 1)
            return
        if name in _WRAPPERS or (name == "timeout" and not self.windows) or (name == "env" and args):
            return self.wrapper(name, args, depth)
        if name in _STARTERS:
            return self.starter(name, args, depth)
        if name in ("python", "py", "pythonw", "python3"):
            return self.python(name, args, depth)
        if name in ("node", "nodejs"):
            return self.node(args)
        if name in _PACKAGE_MANAGERS:
            return self.package_manager(name, args, depth)
        if name in ("npx", "bunx", "pnpx"):
            self.step(f"{_label(name, _first_word(args))}: runs a package, downloading it first if needed.")
            self.warn("install", f"{name} may download and run a package from the internet.")
            return
        if name in _PYTHON_MANAGERS:
            return self.python_manager(name, args, depth)
        if name == "git":
            return self.git(args)
        if name == "ollama":
            return self.ollama(args)
        if name == "docker" or name == "podman":
            self.step(f"{name}: runs containers; it may download images.")
            self.warn("online", f"{name} may download container images from the internet.")
            self.warn("system", f"{name} runs containers, which can change this computer.")
            return
        if name == "make":
            self.step("make: runs build steps from the project's Makefile.")
            self.warn("script", "make runs steps from the Makefile, which AI Assist did not check.")
            return
        if name in _BUILD_TOOLS:
            sub = _first_word(args)
            self.step(f"{_label(name, sub)}: {_BUILD_TOOLS[name]}; builds, tests or runs the project.")
            if sub not in ("fmt", "vet", "--version", "version", "-v", "help", "check", "clippy", "doc"):
                self.warn("online", f"{name} may download what the project needs from the internet.")
            self.paths_in(args, "used")
            return
        if name in _CHECKERS:
            text = _CHECKERS[name]
            effect = "written" if name in _FORMATTERS or (name in ("prettier", "eslint", "ruff") and
                                                          any(a in ("--write", "-w", "--fix", "format") for a in args)) else "used"
            if effect == "written" and "rewrites" not in text:
                text += " (rewrites files)"
            self.step(f"{name}: {text}.")
            self.paths_in(args, effect)
            return
        if name == "find" and not self.windows:
            return self.find(args, depth)
        if name == "timeout":
            self.step("timeout: waits.")
            return
        if name in _PROGRAMS:
            return self.known(name, token, args)
        self.step(f"{token}: a program AI Assist does not know.")
        self.warn("unknown", f"Unknown program: {token}. Check what it does before running it.")
        self.paths_in(args, "used")

    def known(self, name, token, args):
        kind, text = _PROGRAMS[name]
        flags = [arg.lower() for arg in args if arg.startswith(("-", "/"))]
        # Words after a flag that takes a non-file value (-Value 'hello', -Filter *.log) are not files.
        targets = [arg for index, arg in enumerate(args) if not _is_flag(arg, self.windows)
                   and not (index and args[index - 1].lower() in _VALUE_FLAGS)]
        shown = token if len(token) <= 40 else name
        if name in ("sed", "grep", "egrep", "rg", "ag", "awk", "findstr") and targets \
                and not any(flag in ("-e", "-f", "--regexp", "--file", "--expression") for flag in flags):
            targets = targets[1:]  # the first word is the pattern or program, not a file
        if name == "sed" and any(flag.startswith("-i") or flag == "--in-place" for flag in flags):
            text, kind = "changes text inside files", "write"
        elif name == "sed":
            kind = "read"
        if name == "tar" and not any("x" in flag or "c" in flag for flag in flags) and args and not args[0].startswith("-"):
            kind = "write"
        self.step(f"{shown}: {text}.")
        if kind == "read" or kind == "wait":
            if name in ("cd", "chdir", "set-location", "sl", "pushd", "push-location") and targets:
                self.path(targets[0], "used")
            elif name not in ("echo", "write-output", "write", "write-host", "printf", "print"):
                self.paths_in(targets, "used", loose=False)
        elif kind == "write":
            self.paths_in(targets, "written", loose=True)
        elif kind in ("copy", "move"):
            paths = [arg for arg in targets if self.looks_like_path(arg, loose=True)]
            for index, arg in enumerate(paths):
                last = index == len(paths) - 1 and len(paths) > 1
                self.path(arg, "written" if last else ("moved" if kind == "move" else "used"))
            if name == "robocopy" and any(flag in ("/mir", "/purge") for flag in flags):
                self.warn("delete", "robocopy /MIR or /PURGE deletes files in the destination that are not in the source.")
            if kind == "move":
                self.warn("delete", f"{shown} moves or renames files; anything already at the destination is replaced.")
        elif kind == "delete":
            for arg in targets:
                self.path(arg, "deleted")
            listed = ", ".join(targets[:3]) + ("…" if len(targets) > 3 else "") if targets else "the files passed to it"
            self.warn("delete", f"Deletes files: {shown} {listed}.")
        elif kind in ("online", "install"):
            self.warn("online", f"Goes online: {shown} {text}.")
            if kind == "install":
                self.warn("install", f"Installs software with {shown}. Installing can run code from the internet.")
            self.download_targets(name, args)
        elif kind == "admin":
            self.warn("admin", f"Asks for administrator rights ({shown}).")
        elif kind == "system":
            self.warn("system", f"Changes this computer: {shown} {text}.")
        elif kind == "hidden":
            self.warn("hidden", f"{shown} {text}, so AI Assist cannot read what actually runs.")

    # ---------------------------------------------------------------- programs with their own rules
    def shell(self, name, args, depth):
        lowered = [arg.lower() for arg in args]
        if "-executionpolicy" in lowered or "-ep" in lowered:
            self.warn("system", f"{name} -ExecutionPolicy changes which scripts PowerShell allows for this run.")
        if name.startswith(("powershell", "pwsh")) and any(re.fullmatch(r"-(e|ec|en|enc|enco\w*)", flag) for flag in lowered):
            self.step(f"{name}: starts PowerShell with an encoded command.")
            self.warn("hidden", "Contains an encoded PowerShell command that AI Assist cannot read.")
            return
        for index, flag in enumerate(lowered):
            if flag in ("-command", "-c", "/c", "/k", "-commandwithargs") and index + 1 < len(args):
                inner = " ".join(args[index + 1:])
                self.step(f"{name}: starts another shell to run: {inner[:120]}")
                inner_windows = name.startswith(("powershell", "pwsh", "cmd"))
                saved = self.windows, self.paths
                self.windows, self.paths = inner_windows, (ntpath if inner_windows else posixpath)
                try:
                    self.command(inner, depth + 1)
                finally:
                    self.windows, self.paths = saved
                return
            if flag in ("-file", "-f") and index + 1 < len(args):
                self.path(args[index + 1], "used")
                self.step(f"{name}: runs the script file {args[index + 1]}.")
                self.warn("script", f"Runs {args[index + 1]}, a file whose content AI Assist did not check.")
                return
        values = {"-executionpolicy", "-ep", "-windowstyle", "-w", "-outputformat", "-inputformat", "-version", "-workingdirectory"}
        scripts = [arg for index, arg in enumerate(args) if not arg.startswith("-") and not _is_flag(arg, True)
                   and not (index and lowered[index - 1] in values)]
        if scripts:
            self.path(scripts[0], "used")
            self.step(f"{name}: runs the script file {scripts[0]}.")
            self.warn("script", f"Runs {scripts[0]}, a file whose content AI Assist did not check.")
            return
        self.step(f"{name}: starts another shell.")
        self.warn("hidden", f"{name} runs commands it receives from elsewhere (for example the step before it), "
                            f"which AI Assist cannot read.")

    def wrapper(self, name, args, depth):
        rest = list(args)
        if name == "env":
            while rest and (rest[0].startswith("-") or "=" in rest[0]):
                rest.pop(0)
        elif name == "timeout":
            while rest and (rest[0].startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", rest[0])):
                rest.pop(0)
        else:
            while rest and rest[0].startswith("-"):
                rest.pop(0)
        if name == "xargs":
            self.step("xargs: runs the next program once for each line it receives.")
        if rest:
            self.program(rest[0], rest[1:], depth + 1)

    def starter(self, name, args, depth):
        lowered = [arg.lower() for arg in args]
        target = None
        for index, flag in enumerate(lowered):
            if flag in ("-filepath", "-path") and index + 1 < len(args):
                target = args[index + 1]
            if flag == "-verb" and index + 1 < len(args) and args[index + 1].lower() == "runas":
                self.warn("admin", f"Asks for administrator rights ({name} -Verb RunAs).")
        if target is None:
            positional = [arg for arg in args if not arg.startswith("-")]
            target = positional[0] if positional else None
        if not target:
            self.step(f"{name}: starts another program.")
            return
        if _URL.fullmatch(target):
            self.step(f"{name}: opens {target} in another program.")
            self.warn("online", f"Opens a web address ({target}) in another program.")
            return
        self.step(f"{name}: starts {target} as a separate program.")
        self.program(target, [], depth + 1)

    def python(self, name, args, depth):
        index = 0
        unchecked = False
        while index < len(args) and args[index].startswith("-") and args[index] not in ("-m", "-c"):
            flag = args[index]
            if flag in ("--version", "-V", "-VV"):
                self.step(f"{name}: shows Python's version.")
                return
            if re.fullmatch(r"-[A-Za-z]+", flag) and set(flag[1:]) & set("IES"):
                unchecked = True
            if flag in ("-X", "-W") and index + 1 < len(args):
                index += 1
            index += 1
        if unchecked:
            self.warn("online", "Starts Python with -I, -E or -S: then the internet gate cannot check that Python either.")
        note = "" if unchecked else " Python's own internet use is still checked by the internet gate."
        rest = args[index:]
        if not rest:
            self.step(f"{name}: starts Python.")
            return
        if rest[0] == "-m" and len(rest) > 1:
            module, more = rest[1], rest[2:]
            if module == "pip":
                return self.python_manager("pip", more, depth)
            text = {
                "pytest": "runs the tests", "unittest": "runs the tests", "doctest": "runs the tests",
                "venv": "creates a virtual environment folder", "virtualenv": "creates a virtual environment folder",
                "http.server": "starts a web server on this computer", "json.tool": "formats JSON",
                "compileall": "compiles the Python files", "py_compile": "compiles a Python file",
                "black": "formats Python code (rewrites files)", "isort": "sorts imports (rewrites files)",
                "ruff": "checks or formats Python code", "mypy": "checks the code", "flake8": "checks the code",
                "pylint": "checks the code", "build": "builds a package", "twine": "uploads a package to the internet",
                "ensurepip": "installs pip", "timeit": "times a piece of code", "cProfile": "measures where time goes",
            }.get(module, f"runs the Python module {module}")
            self.step(f"{name} -m {module}: {text}.{note}")
            if module == "twine":
                self.warn("online", "twine uploads a package to the internet.")
            if module == "http.server":
                self.warn("system", "Starts a web server; other programs on this computer can reach it.")
            if module in ("venv", "virtualenv", "black", "isort") or (module == "ruff" and "format" in more):
                self.paths_in(more, "written")
            else:
                self.paths_in(more, "used")
            return
        if rest[0] == "-c":
            code = " ".join(rest[1:2])
            self.step(f"{name} -c: runs a short Python program.{note}")
            self.code_hints(code, "Python")
            return
        self.path(rest[0], "used")
        self.step(f"{name} {rest[0]}: runs the Python file {rest[0]}.{note}")
        self.paths_in(rest[1:], "used", loose=False)

    def node(self, args):
        positional = [arg for arg in args if not arg.startswith("-")]
        flag = next((arg for arg in args if arg in ("-e", "--eval", "-p", "--print")), None)
        if flag:
            index = args.index(flag) + 1
            code = args[index] if index < len(args) else ""
            self.step("node -e: runs a short JavaScript program.")
            self.code_hints(code, "JavaScript")
            return
        if any(arg in ("--version", "-v") for arg in args):
            self.step("node: shows Node.js's version.")
            return
        if positional:
            self.path(positional[0], "used")
            self.step(f"node {positional[0]}: runs the JavaScript file {positional[0]}.")
            return
        self.step("node: starts Node.js.")

    def code_hints(self, code, language):
        if re.search(r"\b(remove|unlink|rmtree|rmdir|rm_rf|unlinkSync|rmSync)\b", code):
            self.warn("delete", f"The {language} code may delete files.")
        if re.search(r"open\([^)]*['\"][wax]\+?b?['\"]|write_text|write_bytes|writeFile|\.write\(", code):
            self.step(f"The {language} code writes to files.")
        if re.search(r"\b(subprocess|os\.system|Popen|child_process|exec\()", code):
            self.warn("hidden", f"The {language} code starts other programs, which AI Assist cannot read.")
        if language == "JavaScript" and re.search(r"\b(fetch|https?\.|net\.|axios)\b", code):
            self.warn("online", "The JavaScript code may go online; the internet gate cannot check Node.js.")

    def package_manager(self, name, args, depth):
        sub = _first_word(args)
        rest = args[args.index(sub) + 1:] if sub in args else []
        installs = {"install", "i", "ci", "add", "update", "up", "upgrade", "install-test", "it", "dedupe", "rebuild"}
        if any(arg in ("-g", "--global") for arg in args):
            self.warn("system", f"{name} -g changes packages for the whole computer, not only this project.")
        if (name == "yarn" and not sub) or sub in installs:
            self.step(f"{name} {sub or 'install'}: installs packages from the internet.")
            self.warn("online", f"Goes online: {name} {sub or 'install'} downloads packages.")
            self.warn("install", f"Installs software with {name}. Installing packages can run their setup scripts.")
            return
        if sub in ("run", "run-script") and not rest:
            self.step(f"{name} {sub}: lists the project's scripts.")
            return
        if sub in ("uninstall", "remove", "rm", "un", "r", "prune"):
            self.step(f"{name} {sub}: removes packages from the project.")
            return
        if sub in ("exec", "x", "dlx", "create", "init") and (sub != "init" or rest):
            self.step(f"{name} {sub}: runs a package, downloading it first if needed.")
            self.warn("install", f"{name} {sub} may download and run a package from the internet.")
            return
        if sub in ("publish", "login", "adduser", "deprecate", "unpublish", "owner", "access", "token"):
            self.step(f"{name} {sub}: works with your account on the package website.")
            self.warn("online", f"Goes online: {name} {sub} sends to the package website.")
            return
        if sub in ("audit", "outdated", "view", "info", "show", "search", "whoami", "ping", "doctor"):
            self.step(f"{name} {sub}: reads from the package website.")
            self.warn("online", f"Goes online: {name} {sub} reads from the package website.")
            return
        if sub in ("list", "ls", "ll", "la", "--version", "-v", "version", "help", "config", "root", "bin", "prefix", "why", "explain", "init"):
            self.step(f"{name} {sub}: shows information about the project's packages." if sub != "init"
                      else f"{name} init: creates a package.json file.")
            return
        script = rest[0] if sub in ("run", "run-script", "rum", "urn") and rest else sub
        if not script:
            self.step(f"{name}: shows its help.")
            return
        self.step(f"{name} {'run ' if sub in ('run', 'run-script') else ''}{script}: runs the project's '{script}' script from package.json.")
        self.warn("script", f"Runs the '{script}' script from package.json, which AI Assist did not check.")

    def python_manager(self, name, args, depth):
        sub = _first_word(args)
        rest = args[args.index(sub) + 1:] if sub in args else []
        if name == "uv" and sub == "pip":
            return self.python_manager("pip", rest, depth)
        if sub in ("run", "exec", "shell") and name in ("uv", "poetry", "pipenv", "pdm", "hatch", "conda", "mamba", "pipx"):
            self.step(f"{name} {sub}: runs a program in the project's environment.")
            if name == "pipx":
                self.warn("install", "pipx run may download and run a package from the internet.")
            while rest and rest[0].startswith("-"):
                rest.pop(0)
            if rest and sub != "shell":
                self.program(rest[0], rest[1:], depth + 1)
            return
        if sub in ("install", "add", "download", "sync", "lock", "update", "upgrade", "wheel", "create", "inject", "tool", "venv") \
                and not (name == "uv" and sub == "venv"):
            self.step(f"{name} {sub}: installs Python packages from the internet.")
            self.warn("online", f"Goes online: {name} {sub} downloads packages.")
            self.warn("install", f"Installs software with {name}. Installing packages can run their setup code.")
            return
        if name == "uv" and sub == "venv":
            self.step("uv venv: creates a virtual environment folder.")
            return
        if sub in ("uninstall", "remove", "rm"):
            self.step(f"{name} {sub}: removes Python packages.")
            return
        if sub == "publish" or (name == "poetry" and sub == "publish"):
            self.step(f"{name} publish: uploads a package to the internet.")
            self.warn("online", f"Goes online: {name} publish uploads a package.")
            return
        self.step(f"{_label(name, sub)}: shows information about Python packages.")

    def git(self, args):
        rest = list(args)
        while rest and rest[0].startswith("-"):
            flag = rest.pop(0)
            if flag in ("-C", "-c") and rest:
                rest.pop(0)
        sub = rest[0].lower() if rest else ""
        more = rest[1:]
        flags = [arg for arg in more if arg.startswith("-")]
        texts = {
            "status": "shows which files changed (changes nothing)", "diff": "shows the changes (changes nothing)",
            "log": "shows the history (changes nothing)", "show": "shows a saved change (changes nothing)",
            "blame": "shows who changed each line (changes nothing)", "grep": "searches the files (changes nothing)",
            "ls-files": "lists the tracked files (changes nothing)", "rev-parse": "reads git's details (changes nothing)",
            "describe": "names the current version (changes nothing)", "shortlog": "summarizes the history (changes nothing)",
            "reflog": "shows recent moves of the history (changes nothing)", "add": "marks changes to be saved in the history",
            "commit": "saves a snapshot in the history", "init": "starts a history in this folder",
            "stash": "puts unsaved changes aside", "checkout": "switches files to another version",
            "switch": "switches to another branch", "restore": "puts files back to a saved version",
            "merge": "combines two histories", "rebase": "rewrites the history", "cherry-pick": "copies a saved change",
            "revert": "adds a change that undoes another", "branch": "lists or changes branches", "tag": "lists or adds tags",
            "rm": "deletes files and records it", "mv": "moves or renames files", "clean": "deletes files git does not track",
            "reset": "moves the history back", "config": "changes git settings", "gc": "tidies git's storage",
            "worktree": "manages extra working folders", "push": "sends your history to an online copy",
            "pull": "downloads changes and merges them", "fetch": "downloads changes", "clone": "downloads a copy of a project",
            "remote": "lists or changes the online copies", "submodule": "manages included projects",
            "ls-remote": "reads an online copy", "archive": "packs files into an archive", "apply": "applies a patch to files",
            "am": "applies patches from emails", "bisect": "searches the history for a change", "notes": "adds notes to changes",
            "--version": "shows git's version", "version": "shows git's version", "help": "shows help",
        }
        if not sub:
            self.step("git: shows its help.")
            return
        self.step(f"git {sub}: {texts.get(sub, 'a git command AI Assist does not describe')}.")
        if sub not in texts:
            self.warn("unknown", f"Unknown git command: git {sub}. Check what it does before running it.")
        if sub in ("push", "pull", "fetch", "clone", "ls-remote") or (sub == "submodule" and "update" in more):
            self.warn("online", f"Goes online: git {sub} {texts[sub]}.")
        if sub == "push" and any(flag in ("-f", "--force", "--force-with-lease", "--mirror", "--delete", "-d") for flag in flags):
            self.warn("delete", "git push with --force or --delete can overwrite or delete the online history.")
        if sub == "reset" and "--hard" in flags:
            self.warn("delete", "git reset --hard discards unsaved changes to files.")
        if sub == "clean":
            self.warn("delete", "git clean deletes files that git does not track.")
        if sub == "rm":
            self.warn("delete", "git rm deletes files.")
        if sub == "restore" or (sub == "checkout" and ("--" in more or "." in more or "-f" in flags or "--force" in flags)):
            self.warn("delete", f"git {sub} can discard unsaved changes to files.")
        if sub == "branch" and any(flag in ("-d", "-D", "--delete") for flag in flags):
            self.warn("delete", "git branch -d deletes a branch.")
        if sub == "stash" and more[:1] in (["drop"], ["clear"]):
            self.warn("delete", f"git stash {more[0]} deletes changes put aside.")
        if sub in ("rebase", "filter-branch", "filter-repo"):
            self.warn("delete", f"git {sub} rewrites the history.")
        if sub in ("add", "rm", "mv", "restore", "checkout", "diff", "log", "blame", "show"):
            self.paths_in([arg for arg in more if arg != "--"], "deleted" if sub == "rm" else "used", loose=False)

    def ollama(self, args):
        sub = _first_word(args)
        if sub in ("pull", "push", "signin", "signout"):
            self.step(f"ollama {sub}: {'downloads a model from' if sub == 'pull' else 'sends to'} the internet.")
            self.warn("online", f"Goes online: ollama {sub}.")
        elif sub == "rm":
            self.step("ollama rm: deletes an installed model.")
            self.warn("delete", "ollama rm deletes an installed model.")
        elif sub in ("serve", "start"):
            self.step("ollama serve: starts the Ollama server.")
        else:
            self.step(f"{_label('ollama', sub)}: uses the local models.")

    def find(self, args, depth):
        self.step("find: searches for files.")
        if "-delete" in args:
            self.warn("delete", "find -delete deletes every file it finds.")
        for flag in ("-exec", "-execdir", "-ok", "-okdir"):
            if flag in args:
                start = args.index(flag) + 1
                end = next((i for i in range(start, len(args)) if args[i] in (";", "+")), len(args))
                inner = [arg for arg in args[start:end] if arg != "{}"]
                if inner:
                    self.program(inner[0], inner[1:], depth + 1)
        roots = []
        for arg in args:
            if arg.startswith("-") or arg in ("(", ")", "!"):
                break
            roots.append(arg)
        self.paths_in(roots, "used", loose=True)

    def download_targets(self, name, args):
        """Files that downloaders write or send."""
        lowered = [arg.lower() for arg in args]
        for index, flag in enumerate(lowered):
            if index + 1 >= len(args):
                continue
            if flag in ("-o", "--output", "-outfile", "--output-document", "-p", "--directory-prefix"):
                self.path(args[index + 1], "written")
            elif flag in ("-t", "--upload-file", "-infile"):
                self.path(args[index + 1], "sent")
            elif flag in ("-d", "--data", "--data-binary", "-f", "--form") and "@" in args[index + 1]:
                self.path(args[index + 1].split("@", 1)[1].split(";", 1)[0], "sent")

    # ---------------------------------------------------------------- paths
    def script_path(self, token, name):
        """A file run directly (./build.sh, .\\setup.ps1, tools/run), not a known program."""
        if name in _KNOWN_NAMES:
            return False
        has_folder = "/" in token or "\\" in token
        return has_folder or (token.lower().endswith(_SCRIPTS) and self.exists(token))

    def exists(self, token):
        import os
        try:
            return os.path.lexists(self.full(token))
        except (OSError, ValueError):
            return False

    def full(self, token):
        paths = self.paths
        absolute = paths.isabs(token) or bool(re.match(r"^[A-Za-z]:", token))
        return paths.normpath(token if absolute or not self.root else paths.join(self.root, token))

    def looks_like_path(self, token, loose=False):
        if not token or _URL.search(token) or token.lower() in _NULLS:
            return False
        if token.startswith(("~", "\\\\", "//", "$env:", "%")) or re.match(r"^[A-Za-z]:[\\/]", token):
            return True
        if "/" in token or "\\" in token or token in (".", ".."):
            return not re.fullmatch(r"[\d/]+", token)
        if re.search(r"\.[A-Za-z][A-Za-z0-9]{0,7}$", token) and not token.startswith("."):
            return True
        if token.startswith(".") and len(token) > 1 and re.fullmatch(r"\.[\w.-]+", token):
            return True
        return loose or self.exists(token)

    def paths_in(self, args, effect, loose=False):
        for arg in args:
            if _is_flag(arg, self.windows) or _VALUE.fullmatch(arg) or ("=" in arg and not self.looks_like_path(arg.split("=", 1)[1])):
                continue
            if self.looks_like_path(arg, loose=loose):
                self.path(arg, effect)

    def path(self, token, effect):
        token = token.strip().strip(",")
        if not token or token.lower() in _NULLS or _URL.fullmatch(token) or _VALUE.fullmatch(token):
            return
        here = re.match(r"(?i)^(\$\{?pwd\}?|\$pwd|\$psscriptroot)(?=$|[\\/])", token)
        if here:
            token = "." + token[here.end():]  # the project folder itself
        rank = {"used": 0, "sent": 1, "moved": 2, "written": 3, "deleted": 4}
        lowered = token.lower()
        if token.startswith(("\\\\", "//")):
            self.warn("online", f"Uses a network location: {token}.")
            self.add_file(token, effect, False, False, rank)
            return
        if lowered.startswith(("$env:", "%", "${", "$home", "$")) and not lowered.startswith("$_"):
            self.warn("outside", f"Uses a location from a setting ({token}); check where it points.")
            self.add_file(token, effect, None, False, rank)
            return
        if token.startswith("~"):
            self.warn("outside", f"Uses a path outside the project: {token}.")
            self.add_file(token, effect, False, False, rank)
            return
        if lowered.startswith("$_"):
            return
        try:
            full = self.full(token)
        except (TypeError, ValueError):
            return
        paths = self.paths
        root = self.root
        inside = bool(root) and (paths.normcase(full) == paths.normcase(root)
                                 or paths.normcase(full).startswith(paths.normcase(root).rstrip("\\/") + paths.sep))
        shown = (paths.relpath(full, root) if inside and full != root else ".") if inside else full
        if not inside:
            self.warn("outside", f"Uses a path outside the project: {full}.")
        self.add_file(shown, effect, inside, "*" not in token and "?" not in token and self.exists(token), rank)

    def add_file(self, shown, effect, inside, exists, rank):
        current = self.files.get(shown)
        if current is None:
            self.files[shown] = {"path": shown, "effect": effect, "inside": inside, "exists": exists}
        elif rank[effect] > rank[current["effect"]]:
            current["effect"] = effect


_KNOWN_NAMES = set(_PROGRAMS) | _SHELLS | _WRAPPERS | _STARTERS | set(_BUILD_TOOLS) | set(_CHECKERS) | _PACKAGE_MANAGERS \
    | _PYTHON_MANAGERS | {"python", "py", "pythonw", "node", "git", "npx", "bunx", "pnpx", "docker", "podman", "make",
                          "ollama", "find", "timeout"}


def _label(name, sub):
    return f"{name} {sub}" if sub else name


def _program_name(token):
    name = token.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
    if name.endswith((".exe", ".com")) and name[:-4]:
        name = name[:-4]
    if re.fullmatch(r"python\d+(\.\d+)*w?", name):
        return "python"
    if re.fullmatch(r"pip\d+(\.\d+)*", name):
        return "pip"
    if name == "nodejs":
        return "node"
    return name


def _first_word(args):
    return next((arg for arg in args if not arg.startswith("-")), "")


def _is_flag(arg, windows):
    if arg.startswith("-") and not re.fullmatch(r"-[\d.]+", arg):
        return True
    return windows and bool(re.fullmatch(r"/[A-Za-z?][A-Za-z0-9:]{0,10}", arg))


def _segments(command, windows):
    """Split a shell command, outside quotes, into simple commands.

    Separators are ; | & && || newlines, brackets and $( ... ), so the commands
    inside loops, blocks and substitutions are read on their own. Each token is
    (text, redirect, quoted): redirect is None, "write" (after >) or "read" (after <);
    quoted says the token began with a quote, so it is a value."""
    segments, tokens, current = [], [], []
    started = False
    quote = None
    pending = None
    i, n = 0, len(command)

    quoted = False

    def flush_token():
        nonlocal current, started, pending, quoted
        if started:
            tokens.append(("".join(current), pending, quoted))
            pending = None
        current, started, quoted = [], False, False

    def flush_segment():
        nonlocal tokens
        flush_token()
        if tokens:
            segments.append(tokens)
        tokens = []

    while i < n:
        ch = command[i]
        nxt = command[i + 1] if i + 1 < n else ""
        if quote:
            if ch == quote:
                if windows and quote == "'" and nxt == "'":
                    current.append("'")
                    i += 2
                    continue
                quote = None
            elif quote == '"' and ch == "`" and windows and nxt:
                current.append(nxt)
                i += 2
                continue
            elif quote == '"' and ch == "\\" and not windows and nxt in '"\\$`':
                current.append(nxt)
                i += 2
                continue
            else:
                current.append(ch)
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            quoted = quoted or not started
            started = True
        elif ch == "#" and not started:
            end = command.find("\n", i)
            i = n if end < 0 else end
            continue
        elif (ch == "\\" and not windows) or (ch == "`" and windows):
            if nxt == "\n" or (nxt == "\r" and command[i + 2:i + 3] == "\n"):
                i += 3 if nxt == "\r" else 2
                continue
            if nxt:
                current.append(nxt)
                started = True
                i += 2
                continue
            i += 1
            continue
        elif ch == "`":
            flush_segment()  # POSIX command substitution
        elif ch in "<>":
            text = "".join(current)
            if started and text in ("1", "2", "3", "4", "5", "6", "*", "&"):
                current, started = [], False
            else:
                flush_token()
            j = i
            while j < n and command[j] in "<>":
                j += 1
            if command[j:j + 1] == "&" and command[j + 1:j + 2].isdigit():
                i = j + 2
                continue
            if command[j:j + 1] == "(":
                flush_segment()  # process substitution <( ... )
                i = j + 1
                continue
            if command[j:j + 1] == "&":
                j += 1
            pending = "write" if ">" in command[i:j] else "read"
            i = j
            continue
        elif ch == "$" and nxt == "(":
            flush_segment()
            i += 2
            continue
        elif ch == "&" and windows and nxt != "&" and not tokens and not started:
            pass  # PowerShell's call operator: & 'C:\path\program.exe' args
        elif ch in ";|&(){}\n":
            flush_segment()
        elif ch.isspace():
            flush_token()
        else:
            current.append(ch)
            started = True
        i += 1
    flush_segment()
    return segments
