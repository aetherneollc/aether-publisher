# Runtime directory

This directory is populated at run time and must never be committed.

Expected layout:

```text
runtime/
  accounts.yaml
  data/accounts/<platform>/<account>.json
  data/cache/
  warp/mdm.xml
  warp/...
```

`accounts capture` writes Playwright storage-state JSON files. The WARP
directory contains the Zero Trust enrollment state. Treat both as secrets.
