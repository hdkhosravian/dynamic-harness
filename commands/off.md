---
description: Operator: put the contact cap and the tripwire in shadow mode (they log instead of blocking). Safety hooks stay on.
argument-hint: "[confirm <code> | on]"
disable-model-invocation: true
---
The dynamic-harness hook did not handle this command, so nothing was read or changed.

Reply to the user with exactly this, and do nothing else: "The dynamic-harness hooks are not active in this session, so /dynamic-harness:off did not run. Check that the plugin is enabled and that you accepted the folder trust prompt, then start a new session and try again." Do not read or edit any harness file, and do not run any command.
