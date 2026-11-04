# FAQ

**Does it slow down simple questions?**
No. A quick (T0) question gets a direct answer. Only the always-on safety hooks and the Stop hook's text
check run.

**Why can't Claude just add up the numbers?**
Because a model adding numbers in its head is one of the failures this plugin exists to stop. Every total
comes from a registered check that runs in a sandbox, and the gate replays it before it signs.

**What does "Checked" promise?**
That every part of the work passed its registered check, and that the gate replayed those checks on the files
that were delivered. It does not promise that an assumption or a judgement is right. A forecast always says
"Arithmetic checked; assumptions not."

**Can Claude say "verified" itself?**
No. Words such as "checked", "verified", "confirmed" and "matches" may appear only in the state line the
harness writes. The Stop hook blocks a final message that uses them elsewhere.

**Can it send an email, pay an invoice or publish something?**
Not in v1. Every irreversible action class is denied. The harness prepares the work, and the report says
"I've prepared this. Nothing was sent or changed."

**Why does it ask me so few questions?**
The harness caps them: at most 3 at the start, at most 5 blocking ones per task, and none after half the work
is done. After that point Claude records an assumption and the report names it.

**What happens if a hook crashes?**
The call is denied. Every hook is registered with `onFailure: "block"` and a watchdog, so a crash or a timeout
never counts as a pass.

**Can Claude edit the ledger or forge a receipt?**
It cannot write the run state, and it cannot read the signing key. An edit made some other way breaks the
event chain or a file hash, and the task stops as `STOPPED_TAMPER`. See [security.md](security.md).

**Does it work in Persian?**
Yes. The harness's own messages are written natively in Persian, dates use the Jalali calendar (with
Gregorian next to them), and amounts handle Toman and Rial with a ×10 check. See
[owner-guide.md](owner-guide.md#persian-support).

**Does it work on Windows?**
No. It needs macOS (`sandbox-exec`) or Linux with bubblewrap. WSL2 has not been verified.

**Can I turn some of it off?**
`/dynamic-harness:off` puts the question cap and the tripwire in shadow mode, where they only log. Safety,
budgets, protected paths, the trusted writers and the receipt check always stay on.

**How do I remove it from a folder?**
Type `/dynamic-harness:setup undo` and confirm with the code it shows. Then uninstall the plugin if you want.

**Where are my decision records?**
In the harness's data folder, outside your workspace. Export them with
`/dynamic-harness:export --task <job>` or `/dynamic-harness:export --period <YYYY-MM>`.

**Can I use it in a headless `claude -p` run?**
The hooks run the same way. Questions go through `harness-contact`, and the owner's reply comes as the next
prompt. A full end-to-end `claude -p` run from a request to a signed "Checked" in a set-up folder is still an
open check for this release.
