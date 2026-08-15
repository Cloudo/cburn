# Transcript fixtures

Trimmed and anonymised Claude Code JSONL transcripts of versions 2.1.220-231
(TZ §11): the parser tests and the "parse the history without failures" smoke
test rest on them.

They are generated from the real history on the development machine:

```bash
.venv/bin/python tools/make_fixtures.py
```

The script takes one file per version, cuts a window of 60 records around the
first assistant turn and anonymises it: conversation text, `thinking`, tool
arguments, paths and `toolUseResult` are not kept, identifiers are replaced with
deterministic pseudo-UUIDs, bash commands are collapsed to "first word +
subcommand". Only `usage` is carried over whole - the fixtures exist for it.

The rules for editing by hand are the same: no conversation text and no secrets -
only service fields, `message.usage`, tool names and normalised commands.
