# Repository Tooling

## ESP-IDF

- ESP-IDF version: `v6.0.1`
- ESP-IDF root: `C:\esp\v6.0.1\esp-idf`
- Tools root: `C:\Espressif`
- Base Python: `C:\Users\sxxy4\AppData\Local\Programs\Python\Python310`
- IDF Python environment: `C:\Espressif\tools\python\v6.0.1\venv`

Always run ESP-IDF commands through the repository wrapper:

```powershell
.\scripts\idf.ps1 build
.\scripts\idf.ps1 -p COM11 flash monitor
```

Do not infer that Python is missing when a sandboxed command cannot enumerate
`AppData\Local\Programs`. An `Access denied` result requires running the wrapper
with the appropriate filesystem permission; it is not evidence that Python was
uninstalled. Do not reinstall Python unless the wrapper's explicit dependency
check fails outside the sandbox.
