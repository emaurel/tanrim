# meta_tools / start_subtask

Description for the `start_subtask` MCP tool, shown to any agent allowed to delegate.

`start_subtask` hires a specialist and returns a handle immediately;
`collect_subtask` waits for that handle and returns what was produced. The
pair replaced a single blocking call, so a parent no longer sits idle while
somebody else draws a logo — total time is the longer of the two jobs rather
than their sum.

The real files say to start the subtask BEFORE writing anything (so the wait is
free), to pass the specialist filenames rather than descriptions of what is in
them (a description costs the parent context on every later turn), and not to
touch the deliverable while the specialist is writing it.
